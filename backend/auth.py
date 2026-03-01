"""Modulo de autenticacao — Senha + OTP por e-mail + SQLite (usuarios + cache + jobs)."""

import json
import os
import random
import re
import sqlite3
import smtplib
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from werkzeug.security import generate_password_hash, check_password_hash

from backend.config import (
    DB_PATH, OTP_EXPIRY_MINUTES, OTP_MAX_ATTEMPTS,
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM, log,
)

DEFAULT_STORAGE_LIMIT = 524_288_000  # 500 MB


# ============================================================================
# SQLite
# ============================================================================

@contextmanager
def _db_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


JOB_EXPIRY_DAYS = 7
_db_lock = threading.Lock()


def init_db():
    """Cria tabelas SQLite se nao existirem."""
    with _db_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT    UNIQUE NOT NULL,
                password_hash TEXT,
                created_at    TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS translation_cache (
                source_text      TEXT PRIMARY KEY,
                translated_text  TEXT NOT NULL,
                hit_count        INTEGER DEFAULT 1,
                created_at       TEXT    NOT NULL,
                last_used_at     TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS activity_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_email  TEXT    NOT NULL,
                action      TEXT    NOT NULL,
                details     TEXT,
                ip_address  TEXT,
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS job_history (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id             TEXT    NOT NULL,
                user_email         TEXT    NOT NULL,
                status             TEXT    NOT NULL,
                total_files        INTEGER DEFAULT 0,
                total_strings      INTEGER DEFAULT 0,
                translated_strings INTEGER DEFAULT 0,
                created_at         TEXT    NOT NULL,
                started_at         TEXT,
                finished_at        TEXT,
                expires_at         TEXT    NOT NULL,
                file_available     INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS jobs (
                job_id             TEXT PRIMARY KEY,
                user_email         TEXT NOT NULL,
                status             TEXT NOT NULL DEFAULT 'pending',
                progress           INTEGER DEFAULT 0,
                total_files        INTEGER DEFAULT 0,
                files_done         INTEGER DEFAULT 0,
                total_strings      INTEGER DEFAULT 0,
                translated_strings INTEGER DEFAULT 0,
                errors             TEXT DEFAULT '[]',
                validation         TEXT,
                has_output         INTEGER DEFAULT 0,
                created_at         TEXT NOT NULL,
                started_at         TEXT,
                finished_at        TEXT,
                file_size_bytes    INTEGER DEFAULT 0,
                FOREIGN KEY (user_email) REFERENCES users(email)
            );

            CREATE INDEX IF NOT EXISTS idx_activity_email ON activity_log(user_email);
            CREATE INDEX IF NOT EXISTS idx_activity_date ON activity_log(created_at);
            CREATE INDEX IF NOT EXISTS idx_job_history_email ON job_history(user_email);
            CREATE INDEX IF NOT EXISTS idx_job_history_expires ON job_history(expires_at);
        """)

        # Migracao: adicionar coluna password_hash se nao existir
        try:
            conn.execute("SELECT password_hash FROM users LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
            log.info('[AUTH] Coluna password_hash adicionada (migracao)')

        # Migration: adicionar colunas de quota na tabela users (se nao existirem)
        for col, definition in [
            ('storage_used_bytes', 'INTEGER DEFAULT 0'),
            ('storage_limit_bytes', f'INTEGER DEFAULT {DEFAULT_STORAGE_LIMIT}'),  # 500 MB
        ]:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
            except sqlite3.OperationalError:
                pass  # coluna ja existe

        # Migration: adicionar file_size_bytes na tabela job_history
        try:
            conn.execute("ALTER TABLE job_history ADD COLUMN file_size_bytes INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # coluna ja existe

    log.info('[AUTH] Banco de dados inicializado')


def get_or_create_user(email):
    """Retorna usuario existente ou cria um novo (cadastro automatico)."""
    email = email.strip().lower()
    now = datetime.now().isoformat()
    with _db_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (email, created_at) VALUES (?, ?)",
            (email, now),
        )
        row = conn.execute(
            "SELECT id, email, created_at FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        return dict(row)


def list_all_users():
    """Lista todos os usuarios (para painel admin)."""
    with _db_conn() as conn:
        rows = conn.execute(
            "SELECT id, email, is_admin, created_at FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def get_system_stats():
    """Retorna estatisticas do banco (para painel admin)."""
    with _db_conn() as conn:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]
        cache_count = conn.execute("SELECT COUNT(*) FROM translation_cache").fetchone()[0]
        cache_hits = conn.execute("SELECT SUM(hit_count) FROM translation_cache").fetchone()[0] or 0
    return {
        'users': user_count,
        'admins': admin_count,
        'cache_entries': cache_count,
        'cache_total_hits': cache_hits,
    }


def get_user_by_id(user_id):
    """Busca usuario por ID."""
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT id, email, is_admin, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


# ============================================================================
# Autenticacao por senha
# ============================================================================

# Requisitos minimos da senha
_MIN_PASSWORD_LENGTH = 6


def _validate_password(password):
    """Valida requisitos minimos da senha. Retorna (ok, mensagem)."""
    if not password or len(password) < _MIN_PASSWORD_LENGTH:
        return False, f'Senha deve ter pelo menos {_MIN_PASSWORD_LENGTH} caracteres.'
    return True, None


def register_user(email, password):
    """
    Registra usuario com e-mail + senha.
    Retorna (user_dict, None) se sucesso, (None, erro) se falha.
    Usa scrypt (werkzeug default) — seguro e resistente a brute-force.
    """
    email = email.strip().lower()
    if not email or '@' not in email or '.' not in email.split('@')[-1]:
        return None, 'E-mail invalido.'

    ok, msg = _validate_password(password)
    if not ok:
        return None, msg

    now = datetime.now().isoformat()
    hashed = generate_password_hash(password)

    with _db_conn() as conn:
        existing = conn.execute(
            "SELECT id, password_hash FROM users WHERE email = ?", (email,)
        ).fetchone()

        if existing:
            if existing['password_hash']:
                return None, 'E-mail ja cadastrado. Faca login.'
            # Usuario criado via OTP sem senha — definir senha agora
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE email = ?",
                (hashed, email),
            )
            row = conn.execute(
                "SELECT id, email, created_at FROM users WHERE email = ?",
                (email,),
            ).fetchone()
            log.info(f'[AUTH] Senha definida para usuario existente: {email}')
            return dict(row), None

        conn.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
            (email, hashed, now),
        )
        row = conn.execute(
            "SELECT id, email, created_at FROM users WHERE email = ?",
            (email,),
        ).fetchone()
        log.info(f'[AUTH] Novo usuario registrado: {email}')
        return dict(row), None


def login_user(email, password):
    """
    Autentica usuario com e-mail + senha.
    Retorna (user_dict, None) se sucesso, (None, erro) se falha.
    Usa constant-time comparison (check_password_hash) para evitar timing attacks.
    """
    email = email.strip().lower()
    if not email or not password:
        return None, 'E-mail e senha sao obrigatorios.'

    with _db_conn() as conn:
        row = conn.execute(
            "SELECT id, email, password_hash, created_at FROM users WHERE email = ?",
            (email,),
        ).fetchone()

    if not row:
        # Gastar tempo com hash para evitar timing attack (user enumeration)
        check_password_hash(
            'scrypt:32768:8:1$dummy$0000000000000000000000000000000000000000000000000000000000000000',
            password,
        )
        return None, 'E-mail ou senha incorretos.'

    if not row['password_hash']:
        return None, 'Conta sem senha. Use o codigo por e-mail ou cadastre uma senha.'

    if not check_password_hash(row['password_hash'], password):
        return None, 'E-mail ou senha incorretos.'

    user = {'id': row['id'], 'email': row['email'], 'created_at': row['created_at']}
    log.info(f'[AUTH] Login com senha: {email}')
    return user, None


# ============================================================================
# Jobs — persistencia no SQLite
# ============================================================================

def save_job_db(job_dict):
    """Salva ou atualiza job no SQLite (INSERT OR REPLACE)."""
    try:
        with _db_lock:
            with _db_conn() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO jobs
                       (job_id, user_email, status, progress, total_files, files_done,
                        total_strings, translated_strings, errors, validation,
                        has_output, created_at, started_at, finished_at, file_size_bytes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        job_dict['job_id'],
                        job_dict['user_email'],
                        job_dict['status'],
                        job_dict.get('progress', 0),
                        job_dict.get('total_files', 0),
                        job_dict.get('files_done', 0),
                        job_dict.get('total_strings', 0),
                        job_dict.get('translated_strings', 0),
                        json.dumps(job_dict.get('errors', [])),
                        json.dumps(job_dict.get('validation')) if job_dict.get('validation') else None,
                        1 if job_dict.get('has_output') else 0,
                        job_dict['created_at'],
                        job_dict.get('started_at'),
                        job_dict.get('finished_at'),
                        job_dict.get('file_size_bytes', 0),
                    ),
                )
    except Exception as e:
        log.error(f'[DB] Erro ao salvar job: {e}')


def get_jobs_db(user_email):
    """Retorna lista de jobs do usuario (do mais recente ao mais antigo)."""
    try:
        with _db_lock:
            with _db_conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE user_email = ? ORDER BY created_at DESC",
                    (user_email,),
                ).fetchall()
                return [_row_to_job_dict(r) for r in rows]
    except Exception as e:
        log.error(f'[DB] Erro ao buscar jobs: {e}')
        return []


def get_job_db(job_id):
    """Retorna um job do SQLite ou None."""
    try:
        with _db_lock:
            with _db_conn() as conn:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE job_id = ?", (job_id,),
                ).fetchone()
                return _row_to_job_dict(row) if row else None
    except Exception as e:
        log.error(f'[DB] Erro ao buscar job {job_id}: {e}')
        return None


def delete_job_db(job_id):
    """Remove job do SQLite."""
    try:
        with _db_lock:
            with _db_conn() as conn:
                conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
    except Exception as e:
        log.error(f'[DB] Erro ao deletar job {job_id}: {e}')


def _row_to_job_dict(row):
    """Converte sqlite3.Row da tabela jobs em dict compativel com TranslationJob.to_dict()."""
    d = dict(row)
    # Deserializar JSON
    try:
        d['errors'] = json.loads(d.get('errors') or '[]')
    except (json.JSONDecodeError, TypeError):
        d['errors'] = []
    try:
        d['validation'] = json.loads(d['validation']) if d.get('validation') else None
    except (json.JSONDecodeError, TypeError):
        d['validation'] = None
    d['has_output'] = bool(d.get('has_output'))
    return d


# ============================================================================
# Quota de storage por usuario
# ============================================================================

def get_user_quota(email):
    """Retorna info de quota do usuario."""
    email = email.strip().lower()
    with _db_lock:
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT storage_used_bytes, storage_limit_bytes FROM users WHERE email = ?",
                (email,),
            ).fetchone()
            if not row:
                return {'used_bytes': 0, 'limit_bytes': DEFAULT_STORAGE_LIMIT,
                        'used_mb': 0, 'limit_mb': 500, 'percent': 0}
            used = row['storage_used_bytes'] or 0
            limit = row['storage_limit_bytes'] or DEFAULT_STORAGE_LIMIT
            return {
                'used_bytes': used,
                'limit_bytes': limit,
                'used_mb': round(used / (1024 * 1024), 1),
                'limit_mb': round(limit / (1024 * 1024), 1),
                'percent': round((used / limit) * 100, 1) if limit > 0 else 0,
            }


def update_storage_used(email, delta_bytes):
    """Atualiza storage_used_bytes do usuario (pode ser positivo ou negativo)."""
    email = email.strip().lower()
    try:
        with _db_lock:
            with _db_conn() as conn:
                conn.execute(
                    """UPDATE users
                       SET storage_used_bytes = MAX(0, COALESCE(storage_used_bytes, 0) + ?)
                       WHERE email = ?""",
                    (delta_bytes, email),
                )
    except Exception as e:
        log.error(f'[QUOTA] Erro ao atualizar storage de {email}: {e}')


def check_storage_available(email, new_bytes):
    """Retorna True se o usuario tem espaco disponivel. Falha fechada em caso de erro."""
    try:
        quota = get_user_quota(email)
        return (quota['used_bytes'] + new_bytes) <= quota['limit_bytes']
    except Exception:
        return False


# ============================================================================
# Cache global de traducoes (persistente entre jobs e usuarios)
# ============================================================================


def get_cached_translation_db(source_text):
    """Busca traducao no cache SQLite. Retorna string ou None."""
    try:
        with _db_lock:
            with _db_conn() as conn:
                row = conn.execute(
                    "SELECT translated_text FROM translation_cache WHERE source_text = ?",
                    (source_text,),
                ).fetchone()
                if row:
                    now = datetime.now().isoformat()
                    conn.execute(
                        "UPDATE translation_cache "
                        "SET hit_count = hit_count + 1, last_used_at = ? "
                        "WHERE source_text = ?",
                        (now, source_text),
                    )
                    return row['translated_text']
    except Exception as e:
        log.debug(f'[CACHE] Erro ao buscar cache: {e}')
    return None


def save_cached_translation_db(source_text, translated_text):
    """Salva traducao no cache SQLite (INSERT OR UPDATE)."""
    now = datetime.now().isoformat()
    try:
        with _db_lock:
            with _db_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO translation_cache
                        (source_text, translated_text, hit_count, created_at, last_used_at)
                    VALUES (?, ?, 1, ?, ?)
                    ON CONFLICT(source_text) DO UPDATE SET
                        hit_count    = hit_count + 1,
                        last_used_at = excluded.last_used_at
                    """,
                    (source_text, translated_text, now, now),
                )
    except Exception as e:
        log.debug(f'[CACHE] Erro ao salvar cache: {e}')


def clear_untranslated_cache():
    """Remove entradas do cache onde a traducao e igual ao original."""
    try:
        with _db_lock:
            with _db_conn() as conn:
                deleted = conn.execute(
                    "DELETE FROM translation_cache "
                    "WHERE LOWER(TRIM(source_text)) = LOWER(TRIM(translated_text))"
                ).rowcount
                log.info(f'[CACHE] Limpeza: {deleted} traducoes falhadas removidas do cache')
                return deleted
    except Exception as e:
        log.error(f'[CACHE] Erro ao limpar cache: {e}')
        return 0


# ============================================================================
# Activity Log — registro de acoes por usuario
# ============================================================================

def log_activity(user_email, action, details=None, ip_address=None):
    """Registra acao do usuario no log de atividades."""
    now = datetime.now().isoformat()
    try:
        with _db_lock:
            with _db_conn() as conn:
                conn.execute(
                    "INSERT INTO activity_log (user_email, action, details, ip_address, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (user_email, action, details, ip_address, now),
                )
    except Exception as e:
        log.debug(f'[ACTIVITY] Erro ao registrar atividade: {e}')


def get_user_activity(user_email, limit=50, offset=0):
    """Retorna historico de atividades de um usuario."""
    with _db_conn() as conn:
        rows = conn.execute(
            "SELECT action, details, ip_address, created_at FROM activity_log "
            "WHERE user_email = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (user_email, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_activity(limit=100, offset=0):
    """Retorna historico de atividades de todos os usuarios (admin)."""
    with _db_conn() as conn:
        rows = conn.execute(
            "SELECT user_email, action, details, ip_address, created_at FROM activity_log "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


# ============================================================================
# Job History — historico persistente de jobs
# ============================================================================

def save_job_history(job_dict):
    """Salva job finalizado no historico. Expira em JOB_EXPIRY_DAYS dias."""
    from datetime import timedelta
    now = datetime.now()
    expires = (now + timedelta(days=JOB_EXPIRY_DAYS)).isoformat()
    try:
        with _db_lock:
            with _db_conn() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO job_history
                    (job_id, user_email, status, total_files, total_strings,
                     translated_strings, created_at, started_at, finished_at,
                     expires_at, file_available, file_size_bytes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                    (
                        job_dict['job_id'], job_dict['user_email'], job_dict['status'],
                        job_dict.get('total_files', 0), job_dict.get('total_strings', 0),
                        job_dict.get('translated_strings', 0), job_dict.get('created_at', ''),
                        job_dict.get('started_at'), job_dict.get('finished_at'),
                        expires, job_dict.get('file_size_bytes', 0),
                    ),
                )
    except Exception as e:
        log.debug(f'[JOB_HISTORY] Erro ao salvar: {e}')


def get_user_job_history(user_email, limit=50):
    """Retorna historico de jobs de um usuario."""
    with _db_conn() as conn:
        rows = conn.execute(
            "SELECT job_id, status, total_files, total_strings, translated_strings, "
            "created_at, started_at, finished_at, expires_at, file_available, "
            "COALESCE(file_size_bytes, 0) as file_size_bytes "
            "FROM job_history WHERE user_email = ? AND file_available = 1 "
            "ORDER BY created_at DESC LIMIT ?",
            (user_email, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_job_history(limit=100):
    """Retorna historico de jobs de todos os usuarios (admin)."""
    with _db_conn() as conn:
        rows = conn.execute(
            "SELECT job_id, user_email, status, total_files, total_strings, "
            "translated_strings, created_at, started_at, finished_at, "
            "expires_at, file_available, COALESCE(file_size_bytes, 0) as file_size_bytes "
            "FROM job_history ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_job_history_entry(job_id):
    """Retorna um registro do historico pelo job_id, ou None."""
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT job_id, user_email, status, total_files, total_strings, "
            "translated_strings, created_at, started_at, finished_at, "
            "expires_at, file_available, COALESCE(file_size_bytes, 0) as file_size_bytes "
            "FROM job_history WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        return dict(row) if row else None


def mark_job_files_expired(job_id):
    """Marca file_available=0 para um job especifico no historico."""
    try:
        with _db_lock:
            with _db_conn() as conn:
                conn.execute(
                    "UPDATE job_history SET file_available = 0 WHERE job_id = ?",
                    (job_id,),
                )
    except Exception as e:
        log.error(f'[JOB_HISTORY] Erro ao marcar expirado {job_id}: {e}')


def delete_job_history_entry(job_id):
    """Remove registro do job_history. Activity log ja serve como auditoria."""
    try:
        with _db_lock:
            with _db_conn() as conn:
                conn.execute("DELETE FROM job_history WHERE job_id = ?", (job_id,))
    except Exception as e:
        log.error(f'[JOB_HISTORY] Erro ao deletar {job_id}: {e}')


def get_user_deletable_jobs(user_email, limit=10):
    """Retorna jobs do usuario com arquivos disponiveis, maiores primeiro."""
    with _db_conn() as conn:
        rows = conn.execute(
            "SELECT job_id, COALESCE(file_size_bytes, 0) as file_size_bytes, "
            "created_at, expires_at "
            "FROM job_history WHERE user_email = ? AND file_available = 1 "
            "ORDER BY file_size_bytes DESC LIMIT ?",
            (user_email, limit),
        ).fetchall()
        now = datetime.now().isoformat()
        result = []
        for r in rows:
            d = dict(r)
            d['size_mb'] = round(d['file_size_bytes'] / (1024 * 1024), 1)
            d['expired'] = d['expires_at'] < now
            result.append(d)
        return result


def cleanup_expired_jobs():
    """Remove registros expirados do job_history e retorna job_ids para limpeza de arquivos."""
    now = datetime.now().isoformat()
    try:
        with _db_lock:
            with _db_conn() as conn:
                rows = conn.execute(
                    "SELECT job_id FROM job_history "
                    "WHERE file_available = 1 AND expires_at < ?",
                    (now,),
                ).fetchall()
                expired_ids = [r['job_id'] for r in rows]
                if expired_ids:
                    conn.execute(
                        "DELETE FROM job_history WHERE file_available = 1 AND expires_at < ?",
                        (now,),
                    )
                    log.info(f'[JOB_HISTORY] {len(expired_ids)} jobs expirados removidos')
                return expired_ids
    except Exception as e:
        log.error(f'[JOB_HISTORY] Erro ao limpar expirados: {e}')
        return []


def delete_user_account(user_id):
    """Deleta conta de usuario e seus dados associados. Retorna email ou None."""
    with _db_conn() as conn:
        row = conn.execute("SELECT email, is_admin FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        email = row['email']
        conn.execute("DELETE FROM activity_log WHERE user_email = ?", (email,))
        conn.execute("DELETE FROM job_history WHERE user_email = ?", (email,))
        conn.execute("DELETE FROM jobs WHERE user_email = ?", (email,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        log.info(f'[AUTH] Conta deletada: {email}')
        return email


# ============================================================================
# OTP em memoria
# ============================================================================

_otps = {}           # {email: {code, expires_at, attempts, sent_at}}
_otp_lock = threading.Lock()

OTP_RESEND_SECONDS = 60  # Rate limit de reenvio por e-mail


def generate_otp(email):
    """
    Gera codigo OTP de 6 digitos para o e-mail.
    Retorna (code, 0) se gerado com sucesso.
    Retorna (None, remaining_seconds) se rate limit ativo.
    """
    email = email.strip().lower()
    now = time.time()

    with _otp_lock:
        existing = _otps.get(email)
        if existing and now - existing['sent_at'] < OTP_RESEND_SECONDS:
            remaining = int(OTP_RESEND_SECONDS - (now - existing['sent_at']))
            return None, remaining

        code = f"{random.randint(0, 999999):06d}"
        _otps[email] = {
            'code': code,
            'expires_at': now + OTP_EXPIRY_MINUTES * 60,
            'attempts': 0,
            'sent_at': now,
        }
        return code, 0


def verify_otp(email, code):
    """
    Verifica codigo OTP.
    Retorna (True, None) se valido.
    Retorna (False, 'motivo') se invalido.
    """
    email = email.strip().lower()
    now = time.time()

    with _otp_lock:
        entry = _otps.get(email)

        if not entry:
            return False, 'Nenhum codigo solicitado para este e-mail.'

        if now > entry['expires_at']:
            del _otps[email]
            return False, 'Codigo expirado. Solicite um novo.'

        entry['attempts'] += 1

        if entry['attempts'] > OTP_MAX_ATTEMPTS:
            del _otps[email]
            return False, 'Muitas tentativas. Solicite um novo codigo.'

        if entry['code'] != code.strip():
            remaining = OTP_MAX_ATTEMPTS - entry['attempts']
            if remaining <= 0:
                del _otps[email]
                return False, 'Codigo incorreto. Solicite um novo codigo.'
            return False, f'Codigo incorreto. {remaining} tentativa(s) restante(s).'

        del _otps[email]
        return True, None


# ============================================================================
# Envio de e-mail via smtplib
# ============================================================================

def send_otp_email(email, code):
    """Envia e-mail com codigo OTP. Se SMTP nao configurado, imprime no log."""
    if not SMTP_USER or not SMTP_PASS:
        log.info(f'[AUTH] OTP para {email}: {code}  (SMTP nao configurado — apenas log)')
        return

    subject = f'Seu codigo de acesso: {code}'

    html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#111827;font-family:system-ui,-apple-system,sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td align="center" style="padding:40px 16px">
        <table width="440" cellpadding="0" cellspacing="0"
               style="background:#1f2937;border-radius:12px;border:1px solid #374151">
          <tr>
            <td style="padding:32px 32px 0">
              <div style="display:inline-flex;align-items:center;gap:10px">
                <div style="width:36px;height:36px;background:#2563eb;border-radius:8px;
                            text-align:center;line-height:36px;font-size:18px;
                            font-weight:bold;color:#fff">T</div>
                <span style="font-size:18px;font-weight:600;color:#fff">Traducao</span>
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:24px 32px 8px">
              <p style="margin:0;font-size:22px;font-weight:600;color:#fff">
                Seu codigo de acesso
              </p>
              <p style="margin:8px 0 0;font-size:14px;color:#9ca3af">
                Use o codigo abaixo para entrar.
                Valido por {OTP_EXPIRY_MINUTES} minutos.
              </p>
            </td>
          </tr>
          <tr>
            <td style="padding:24px 32px">
              <div style="background:#111827;border:1px solid #374151;border-radius:8px;
                          text-align:center;padding:20px 16px">
                <span style="font-size:40px;font-weight:700;letter-spacing:14px;
                             color:#fff;font-family:monospace">{code}</span>
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:0 32px 32px">
              <p style="margin:0;font-size:12px;color:#6b7280">
                Se voce nao solicitou este codigo, ignore este e-mail.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = SMTP_FROM
    msg['To'] = email
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    try:
        # Porta 465 = SSL implicito (SMTPS); demais = STARTTLS
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10) as server:
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_FROM, [email], msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
                server.ehlo()
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_FROM, [email], msg.as_string())
        log.info(f'[AUTH] OTP enviado para {email}')
    except Exception as e:
        log.error(f'[AUTH] Erro ao enviar OTP para {email}: {e}')
        raise RuntimeError(f'Erro ao enviar e-mail: {e}')
