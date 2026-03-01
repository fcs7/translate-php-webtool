#!/usr/bin/env python3
"""
Trans-Script Web — Aplicacao Flask principal.
Monolito: serve API REST + WebSocket + frontend React (static).
"""

import os
import re
import threading
import time
from datetime import timedelta
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, send_file, session
from flask_cors import CORS
from flask_socketio import SocketIO, join_room

from backend.config import (
    UPLOAD_FOLDER, JOBS_FOLDER, STATIC_FOLDER, MAX_CONTENT_LENGTH,
    MAX_CONCURRENT_JOBS, RATE_LIMIT_SECONDS, SECRET_KEY, log,
)
from backend.translator import (
    start_translation, start_translation_raw, get_job, delete_job, list_jobs,
    cleanup_old_jobs, count_running_jobs,
)
from backend.auth import (
    init_db, get_or_create_user, list_all_users, get_system_stats, get_user_by_id,
    generate_otp, verify_otp, send_otp_email,
    register_user, login_user,
    clear_untranslated_cache,
    log_activity, get_user_activity, get_all_activity,
    get_user_job_history, get_all_job_history,
    cleanup_expired_jobs, delete_user_account,
    get_user_quota, check_storage_available,
    get_job_db,
    get_job_history_entry, get_user_deletable_jobs, delete_job_history_entry,
)
from backend.admin_auth import (
    init_admin_db, create_admin_session, validate_admin_session,
    revoke_admin_session, revoke_all_admin_sessions,
    is_admin, set_admin, list_admins, list_active_sessions,
    cleanup_expired_sessions,
)
from backend.config import ADMIN_EMAILS

# ============================================================================
# App
# ============================================================================

from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__, static_folder=STATIC_FOLDER, static_url_path='')

# Confiar em 1 proxy (Nginx) para X-Forwarded-For e X-Forwarded-Proto
# Garante que request.remote_addr reflita o IP real do cliente
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
app.secret_key = SECRET_KEY
app.permanent_session_lifetime = timedelta(days=30)

# Session cookie security
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# Em producao (HTTPS): descomentar ou definir via env
# app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', '').lower() in ('1', 'true', 'yes')

CORS(app, supports_credentials=True)

# Inicializar banco de dados
init_db()
init_admin_db()

# Auto-promover admins listados em ADMIN_EMAILS
for admin_email in ADMIN_EMAILS:
    get_or_create_user(admin_email)
    set_admin(admin_email, True)

try:
    import gevent  # noqa: F401
    _async_mode = 'gevent'
except ImportError:
    _async_mode = 'threading'

socketio = SocketIO(app, cors_allowed_origins="*", async_mode=_async_mode)

# ============================================================================
# Cleanup periodico (background thread)
# ============================================================================

_CLEANUP_INTERVAL = 86400  # 24 horas em segundos


def _cleanup_loop():
    """Thread daemon que roda cleanup a cada 24h."""
    import time as _time
    _time.sleep(60)  # esperar app estabilizar
    while True:
        try:
            log.info('[CLEANUP] Iniciando limpeza periodica...')
            from backend.translator import expire_job_files
            total_freed = 0
            total_jobs = 0
            for expired_id in cleanup_expired_jobs():
                freed, _ = expire_job_files(expired_id)
                total_freed += freed
                total_jobs += 1
            cleanup_expired_sessions()
            if total_jobs > 0:
                log.info(f'[CLEANUP] {total_jobs} jobs expirados limpos, '
                         f'{total_freed / (1024*1024):.1f} MB liberados')
            else:
                log.info('[CLEANUP] Nenhum job expirado encontrado')
        except Exception as e:
            log.error(f'[CLEANUP] Erro na limpeza periodica: {e}')
        _time.sleep(_CLEANUP_INTERVAL)


_cleanup_thread = threading.Thread(target=_cleanup_loop, daemon=True, name='cleanup')
_cleanup_thread.start()

# ============================================================================
# Autenticacao
# ============================================================================

def login_required(f):
    """Decorator: exige sessao ativa."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_email' not in session:
            return jsonify({'error': 'Autenticacao necessaria'}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """
    Decorator: exige sessao admin valida.
    Verifica token no header Authorization: Bearer <token>
    com validacao AES-256-GCM + HMAC-SHA256 + IP binding.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Token admin ausente'}), 401

        token = auth_header[7:]
        admin_session = validate_admin_session(token, request.remote_addr)
        if not admin_session:
            return jsonify({'error': 'Sessao admin invalida ou expirada'}), 401

        # Injetar dados do admin no request context
        request.admin_email = admin_session['email']
        request.admin_session = admin_session
        return f(*args, **kwargs)
    return decorated


# Rate limit simples: {ip: timestamp_ultimo_upload}
_upload_timestamps = {}

# Rate limit para tentativas de admin login: {ip: [timestamp, ...]}
_admin_login_attempts = {}
_admin_login_lock = threading.Lock()
_ADMIN_LOGIN_MAX_ATTEMPTS = 5
_ADMIN_LOGIN_WINDOW = 300  # 5 minutos

# Regex para validar job_id (apenas hex, 8 chars)
_JOB_ID_RE = re.compile(r'^[a-f0-9]{8}$')


# ============================================================================
# Seguranca — headers em todas as respostas
# ============================================================================

@app.after_request
def security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response


# ============================================================================
# Logging — toda requisicao
# ============================================================================

@app.before_request
def log_request():
    if request.path.startswith('/api'):
        log.info(f'{request.remote_addr} {request.method} {request.path}')


# ============================================================================
# Helpers
# ============================================================================

def _validate_job_id(job_id):
    """Valida formato do job_id para evitar path traversal."""
    return bool(_JOB_ID_RE.match(job_id))


def _check_rate_limit(ip):
    """Retorna True se o IP esta dentro do rate limit."""
    now = time.time()
    # Limpeza periodica: remove entradas com mais de 1h (evita crescimento indefinido)
    if len(_upload_timestamps) > 1000:
        stale = [k for k, v in _upload_timestamps.items() if now - v > 3600]
        for k in stale:
            del _upload_timestamps[k]
    last = _upload_timestamps.get(ip, 0)
    if now - last < RATE_LIMIT_SECONDS:
        return False
    _upload_timestamps[ip] = now
    return True


def _resolve_job(job_id, require_completed=False):
    """Resolve job da memoria ou DB. Retorna (dict, error_response).
    Se ok: (job_dict, None). Se erro: (None, response_tuple)."""
    job = get_job(job_id)
    if job:
        if job.user_email != session['user_email']:
            return None, (jsonify({'error': 'Acesso negado'}), 403)
        if require_completed and job.status != 'completed':
            return None, (jsonify({'error': 'Traducao ainda nao concluida'}), 400)
        return job.to_dict(), None

    db_job = get_job_db(job_id)
    if not db_job:
        return None, (jsonify({'error': 'Job nao encontrado'}), 404)
    if db_job['user_email'] != session['user_email']:
        return None, (jsonify({'error': 'Acesso negado'}), 403)
    if require_completed and (db_job['status'] != 'completed' or not db_job.get('has_output')):
        return None, (jsonify({'error': 'Traducao ainda nao concluida'}), 400)
    return db_job, None


# ============================================================================
# Rotas de autenticacao
# ============================================================================

@app.route('/api/auth/register', methods=['POST'])
def auth_register():
    """Cadastro com e-mail + senha."""
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    user, error = register_user(email, password)
    if error:
        return jsonify({'error': error}), 400

    user['is_admin'] = is_admin(email)
    session['user_email'] = email
    session.permanent = True
    log_activity(email, 'register', ip_address=request.remote_addr)
    log.info(f'[AUTH] Registro: {email}')
    return jsonify({'user': user}), 201


@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    """Login com e-mail + senha."""
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    user, error = login_user(email, password)
    if error:
        return jsonify({'error': error}), 401

    user['is_admin'] = is_admin(email)
    session['user_email'] = email
    session.permanent = True
    log_activity(email, 'login', ip_address=request.remote_addr)
    log.info(f'[AUTH] Login senha: {email}')
    return jsonify({'user': user}), 200


@app.route('/api/auth/request-otp', methods=['POST'])
def auth_request_otp():
    """Solicita OTP — apenas para contas existentes (recuperacao de senha)."""
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()

    if not email or '@' not in email or '.' not in email.split('@')[-1]:
        return jsonify({'error': 'E-mail invalido'}), 400

    # Verificar se a conta existe — mensagem generica para evitar enumeracao
    from backend.auth import _db_conn
    with _db_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if not row:
        # Nao revelar se o e-mail existe ou nao
        return jsonify({'message': 'Se o e-mail estiver cadastrado, voce recebera um codigo.'}), 200

    code, remaining = generate_otp(email)
    if code is None:
        return jsonify({'error': f'Aguarde {remaining}s para solicitar um novo codigo'}), 429

    try:
        send_otp_email(email, code)
    except RuntimeError as e:
        return jsonify({'error': 'Erro ao enviar e-mail. Tente novamente.'}), 500

    log.info(f'[AUTH] OTP recuperacao solicitado: {email}')
    return jsonify({'message': 'Se o e-mail estiver cadastrado, voce recebera um codigo.'}), 200


@app.route('/api/auth/verify-otp', methods=['POST'])
def auth_verify_otp():
    """Verifica OTP — apenas para contas existentes (recuperacao de senha)."""
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    code = (data.get('code') or '').strip()

    if not email or not code:
        return jsonify({'error': 'E-mail e codigo sao obrigatorios'}), 400

    ok, reason = verify_otp(email, code)
    if not ok:
        return jsonify({'error': reason}), 401

    # Apenas login — nao cria conta nova
    from backend.auth import _db_conn
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT id, email, created_at FROM users WHERE email = ?", (email,)
        ).fetchone()
    if not row:
        return jsonify({'error': 'Conta nao encontrada. Cadastre-se primeiro.'}), 401

    user = dict(row)
    user['is_admin'] = is_admin(email)
    session['user_email'] = email
    session.permanent = True
    log_activity(email, 'login_otp', 'Recuperacao via codigo', ip_address=request.remote_addr)
    log.info(f'[AUTH] Login via recuperacao OTP: {email}')
    return jsonify({'user': user}), 200


@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    email = session.pop('user_email', None)
    if email:
        log_activity(email, 'logout', ip_address=request.remote_addr)
        if is_admin(email):
            revoke_all_admin_sessions(email)
        log.info(f'[AUTH] Logout: {email}')
    session.clear()
    return jsonify({'message': 'Logout realizado'}), 200


@app.route('/api/auth/me')
def auth_me():
    email = session.get('user_email')
    if not email:
        return jsonify({'error': 'Nao autenticado'}), 401
    user = get_or_create_user(email)
    user['is_admin'] = is_admin(email)
    user['quota'] = get_user_quota(email)
    return jsonify({'user': user}), 200


# ============================================================================
# API REST
# ============================================================================

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'service': 'trans-script-web'})


@app.route('/api/upload', methods=['POST'])
@login_required
def upload_file():
    """Recebe arquivo compactado ou arquivos PHP avulsos e inicia traducao."""
    ip = request.remote_addr

    # Rate limit
    if not _check_rate_limit(ip):
        log.warning(f'{ip} rate limited (upload)')
        return jsonify({'error': f'Aguarde {RATE_LIMIT_SECONDS}s entre uploads'}), 429

    # Limite de jobs simultaneos
    running = count_running_jobs()
    if running >= MAX_CONCURRENT_JOBS:
        log.warning(f'{ip} bloqueado: {running} jobs rodando (max {MAX_CONCURRENT_JOBS})')
        return jsonify({'error': f'Limite de {MAX_CONCURRENT_JOBS} traducoes simultaneas'}), 429

    delay = max(0.05, min(float(request.form.get('delay', 0.2)), 5.0))

    # ── Modo 2: multiplos arquivos PHP avulsos ──────────────────────────────
    raw_files = request.files.getlist('files')
    if raw_files:
        paths = request.form.getlist('paths')

        # Validar: todos devem ser .php
        for i, f in enumerate(raw_files):
            if not f.filename or not f.filename.lower().endswith('.php'):
                log.warning(f'{ip} arquivo PHP rejeitado: {f.filename}')
                return jsonify({'error': f'Arquivo nao e .php: {f.filename}'}), 400

        # Salvar arquivos em diretorio temporario preservando caminhos relativos
        tmp_dir = os.path.join(UPLOAD_FOLDER, f"raw_{os.urandom(8).hex()}")
        total_size = 0

        try:
            for i, f in enumerate(raw_files):
                # Usar caminho relativo se fornecido, senao nome do arquivo
                rel_path = paths[i] if i < len(paths) else f.filename
                # Sanitizar: remover prefixo de pasta raiz do webkitdirectory
                # (ex: "minha_pasta/sub/file.php" -> "sub/file.php" ou "file.php")
                parts = rel_path.replace('\\', '/').split('/')
                if len(parts) > 1:
                    # Remover primeiro nivel (nome da pasta selecionada)
                    rel_path = '/'.join(parts[1:])
                else:
                    rel_path = parts[0]

                # Prevenir path traversal
                safe_path = os.path.normpath(rel_path)
                if safe_path.startswith('..') or os.path.isabs(safe_path):
                    return jsonify({'error': f'Caminho invalido: {rel_path}'}), 400

                dest = os.path.join(tmp_dir, safe_path)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                f.save(dest)
                total_size += os.path.getsize(dest)

            log.info(f'{ip} upload: {len(raw_files)} arquivos PHP ({total_size / 1024:.1f} KB)')

            job_id = start_translation_raw(tmp_dir, delay, socketio, user_email=session['user_email'])
            log_activity(session['user_email'], 'upload', f'{len(raw_files)} arquivos PHP, job {job_id}', ip)
            log.info(f'{ip} job criado: {job_id} (delay={delay}s, {len(raw_files)} arquivos PHP)')
            return jsonify({'job_id': job_id}), 201

        except Exception as e:
            log.error(f'{ip} erro ao criar job (PHP avulsos): {e}')
            if os.path.exists(tmp_dir):
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
            return jsonify({'error': 'Erro interno ao processar arquivos'}), 500

    # ── Modo 1: arquivo compactado (ZIP, RAR, TAR) ─────────────────────────
    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado'}), 400

    f = request.files['file']
    allowed = ('.zip', '.rar', '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2')
    if not f.filename or not f.filename.lower().endswith(allowed):
        log.warning(f'{ip} arquivo rejeitado: {f.filename}')
        return jsonify({'error': 'Formatos aceitos: ZIP, RAR, TAR, TAR.GZ ou arquivos .php'}), 400

    ext = '.' + f.filename.rsplit('.', 1)[-1]
    filename = f"upload_{os.urandom(8).hex()}{ext}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    f.save(filepath)

    file_size = os.path.getsize(filepath)
    log.info(f'{ip} upload: {f.filename} ({file_size / 1024:.1f} KB)')

    # Verificar quota de storage
    if not check_storage_available(session['user_email'], file_size):
        os.remove(filepath)
        quota = get_user_quota(session['user_email'])
        deletable = get_user_deletable_jobs(session['user_email'], limit=5)
        log.warning(f'{ip} quota excedida: {quota["used_mb"]} MB / {quota["limit_mb"]} MB')
        return jsonify({
            'error': f'Cota de armazenamento excedida ({quota["used_mb"]} MB / {quota["limit_mb"]} MB). '
                     'Delete traducoes antigas para liberar espaco.',
            'quota': quota,
            'deletable_jobs': deletable,
        }), 413


    try:
        job_id = start_translation(filepath, delay, socketio, user_email=session['user_email'])
        os.remove(filepath)
        log_activity(session['user_email'], 'upload', f'Arquivo compactado, job {job_id}', ip)
        log.info(f'{ip} job criado: {job_id} (delay={delay}s)')
        return jsonify({'job_id': job_id}), 201
    except Exception as e:
        log.error(f'{ip} erro ao criar job: {e}')
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({'error': 'Erro interno ao processar arquivo'}), 500


@app.route('/api/jobs')
@login_required
def get_jobs():
    return jsonify(list_jobs(session['user_email']))


@app.route('/api/jobs/<job_id>')
@login_required
def get_job_status(job_id):
    if not _validate_job_id(job_id):
        return jsonify({'error': 'ID invalido'}), 400
    data, err = _resolve_job(job_id)
    if err:
        return err
    return jsonify(data)


@app.route('/api/jobs/<job_id>/download')
@login_required
def download_job(job_id):
    if not _validate_job_id(job_id):
        return jsonify({'error': 'ID invalido'}), 400
    data, err = _resolve_job(job_id, require_completed=True)
    if err:
        return err
    zip_path = os.path.join(JOBS_FOLDER, job_id, 'output.zip')
    if not os.path.exists(zip_path):
        return jsonify({'error': 'Arquivo de saida nao encontrado (pode ter sido limpo)'}), 410
    log_activity(session['user_email'], 'download', f'Job {job_id}', request.remote_addr)
    log.info(f'{request.remote_addr} download ZIP: {job_id}')
    return send_file(
        zip_path,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'traducao_{job_id}.zip',
    )


@app.route('/api/jobs/<job_id>/download/voipnow')
@login_required
def download_voipnow(job_id):
    """Download do language pack no formato VoipNow (tar.gz)."""
    if not _validate_job_id(job_id):
        return jsonify({'error': 'ID invalido'}), 400
    data, err = _resolve_job(job_id, require_completed=True)
    if err:
        return err
    tar_path = os.path.join(JOBS_FOLDER, job_id, 'voipnow.tar.gz')
    if not os.path.exists(tar_path):
        return jsonify({'error': 'Arquivo VoipNow nao encontrado'}), 410
    log.info(f'{request.remote_addr} download VoipNow: {job_id}')
    return send_file(
        tar_path,
        mimetype='application/gzip',
        as_attachment=True,
        download_name=f'voipnow_pt_br_{job_id}.tar.gz',
    )


@app.route('/api/jobs/<job_id>', methods=['DELETE'])
@login_required
def remove_job(job_id):
    if not _validate_job_id(job_id):
        return jsonify({'error': 'ID invalido'}), 400
    data, err = _resolve_job(job_id)
    if err:
        return err
    delete_job(job_id)
    log_activity(session['user_email'], 'delete_job', f'Job {job_id}', request.remote_addr)
    log.info(f'{request.remote_addr} deletou job: {job_id}')
    return jsonify({'message': 'Job removido'})


@app.route('/api/jobs/<job_id>/cancel', methods=['POST'])
@login_required
def cancel_job(job_id):
    if not _validate_job_id(job_id):
        return jsonify({'error': 'ID invalido'}), 400
    job = get_job(job_id)
    if not job:
        return jsonify({'error': 'Job nao encontrado'}), 404
    if job.user_email != session['user_email']:
        return jsonify({'error': 'Acesso negado'}), 403
    if job.status != 'running':
        return jsonify({'error': 'Job nao esta em execucao'}), 400
    job.cancel()
    log_activity(session['user_email'], 'cancel_job', f'Job {job_id}', request.remote_addr)
    log.info(f'{request.remote_addr} cancelou job: {job_id}')
    return jsonify({'message': 'Cancelamento solicitado'})


# ============================================================================
# Manutencao de cache
# ============================================================================

@app.route('/api/cache/clear-untranslated', methods=['POST'])
@login_required
def clear_cache():
    """Remove traducoes falhadas do cache (source == translated)."""
    deleted = clear_untranslated_cache()
    log.info(f'{request.remote_addr} limpou cache: {deleted} entradas removidas')
    return jsonify({'deleted': deleted, 'message': f'{deleted} traducoes falhadas removidas do cache'})


@app.route('/api/engine/stats')
@login_required
def engine_stats():
    """Retorna metricas da engine de traducao (providers, cache, status)."""
    from backend.engine import get_engine
    return jsonify(get_engine().get_stats())


# ============================================================================
# Admin — Sessao
# ============================================================================

@app.route('/api/admin/login', methods=['POST'])
@login_required
def admin_login():
    """
    Gera token admin seguro para usuario que ja esta logado e e admin.
    Token: 384-bit entropy + HMAC-SHA256 + sessao server-side com AES-256-GCM.
    """
    ip = request.remote_addr

    # Rate limit de tentativas de admin login por IP (thread-safe)
    now = time.time()
    with _admin_login_lock:
        attempts = _admin_login_attempts.get(ip, [])
        attempts = [t for t in attempts if now - t < _ADMIN_LOGIN_WINDOW]
        if len(attempts) >= _ADMIN_LOGIN_MAX_ATTEMPTS:
            log.warning(f'[ADMIN] Rate limit atingido para IP {ip}')
            return jsonify({'error': 'Muitas tentativas. Aguarde 5 minutos.'}), 429

    email = session['user_email']

    if not is_admin(email):
        with _admin_login_lock:
            attempts = _admin_login_attempts.get(ip, [])
            attempts = [t for t in attempts if now - t < _ADMIN_LOGIN_WINDOW]
            attempts.append(now)
            _admin_login_attempts[ip] = attempts
        log.warning(f'[ADMIN] Tentativa de login admin negada: {email} ({ip})')
        return jsonify({'error': 'Acesso negado'}), 403

    token = create_admin_session(email, ip)
    if not token:
        return jsonify({'error': 'Erro ao criar sessao admin'}), 500

    # Limpar tentativas em caso de sucesso
    with _admin_login_lock:
        _admin_login_attempts.pop(ip, None)

    return jsonify({
        'token': token,
        'message': 'Sessao admin criada',
    }), 200


@app.route('/api/admin/logout', methods=['POST'])
@admin_required
def admin_logout():
    """Revoga sessao admin atual."""
    auth_header = request.headers.get('Authorization', '')
    token = auth_header[7:]  # Remove 'Bearer '
    revoke_admin_session(token)
    return jsonify({'message': 'Sessao admin revogada'}), 200


@app.route('/api/admin/me')
@admin_required
def admin_me():
    """Retorna dados da sessao admin."""
    return jsonify({
        'email': request.admin_email,
        'is_admin': True,
        'session': {
            'created_at': request.admin_session['created_at'],
            'ip': request.admin_session['ip'],
        },
    })


# ============================================================================
# Admin — Gestao de usuarios
# ============================================================================

@app.route('/api/admin/users')
@admin_required
def admin_list_users():
    """Lista todos os usuarios com status admin."""
    return jsonify(list_all_users())


@app.route('/api/admin/users/<int:user_id>/toggle-admin', methods=['POST'])
@admin_required
def admin_toggle_user(user_id):
    """Promove ou rebaixa usuario a admin."""
    row = get_user_by_id(user_id)
    if not row:
        return jsonify({'error': 'Usuario nao encontrado'}), 404

    new_status = not bool(row['is_admin'])

    # Impedir remocao do ultimo admin
    if not new_status:
        current_admins = list_admins()
        if len(current_admins) <= 1:
            return jsonify({'error': 'Impossivel remover o ultimo admin do sistema'}), 400

    set_admin(row['email'], new_status)

    # Revogar sessoes ativas ao rebaixar admin (evita acesso pos-democao)
    if not new_status:
        revoke_all_admin_sessions(row['email'])

    action = 'promovido a admin' if new_status else 'removido de admin'
    return jsonify({'message': f'{row["email"]} {action}', 'is_admin': new_status})


@app.route('/api/admin/admins')
@admin_required
def admin_list_admins():
    """Lista todos os admins."""
    return jsonify(list_admins())


@app.route('/api/admin/sessions')
@admin_required
def admin_list_sessions():
    """Lista sessoes admin ativas."""
    return jsonify(list_active_sessions())


@app.route('/api/admin/sessions/revoke-all', methods=['POST'])
@admin_required
def admin_revoke_all():
    """Revoga todas as sessoes de um admin (exceto a propria)."""
    data = request.get_json(silent=True) or {}
    target_email = data.get('email', '').strip().lower()
    if not target_email:
        return jsonify({'error': 'E-mail obrigatorio'}), 400
    count = revoke_all_admin_sessions(target_email)
    return jsonify({'revoked': count, 'message': f'{count} sessoes revogadas'})


# ============================================================================
# Admin — Gestao de jobs (todos os usuarios)
# ============================================================================

@app.route('/api/admin/jobs')
@admin_required
def admin_list_all_jobs():
    """Lista todos os jobs de todos os usuarios (visao admin)."""
    all_jobs = list_jobs()  # Sem filtro de email
    return jsonify(all_jobs)


@app.route('/api/admin/stats')
@admin_required
def admin_stats():
    """Estatisticas gerais do sistema."""
    stats = get_system_stats()
    active_sessions_list = list_active_sessions()

    stats.update({
        'running_jobs': count_running_jobs(),
        'max_concurrent_jobs': MAX_CONCURRENT_JOBS,
        'active_admin_sessions': len(active_sessions_list),
    })

    return jsonify(stats)


@app.route('/api/admin/activity')
@admin_required
def admin_activity():
    """Log de atividades global (admin)."""
    limit = min(int(request.args.get('limit', 100)), 500)
    offset = int(request.args.get('offset', 0))
    return jsonify(get_all_activity(limit, offset))


@app.route('/api/admin/users/<int:user_id>/activity')
@admin_required
def admin_user_activity(user_id):
    """Log de atividades de um usuario especifico."""
    row = get_user_by_id(user_id)
    if not row:
        return jsonify({'error': 'Usuario nao encontrado'}), 404
    limit = min(int(request.args.get('limit', 50)), 200)
    return jsonify(get_user_activity(row['email'], limit))


@app.route('/api/admin/users/<int:user_id>/history')
@admin_required
def admin_user_history(user_id):
    """Historico de jobs de um usuario especifico."""
    row = get_user_by_id(user_id)
    if not row:
        return jsonify({'error': 'Usuario nao encontrado'}), 404
    return jsonify(get_user_job_history(row['email']))


@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@admin_required
def admin_delete_user(user_id):
    """Deleta conta de usuario."""
    row = get_user_by_id(user_id)
    if not row:
        return jsonify({'error': 'Usuario nao encontrado'}), 404
    if row['is_admin']:
        current_admins = list_admins()
        if len(current_admins) <= 1:
            return jsonify({'error': 'Impossivel deletar o ultimo admin'}), 400
    email = delete_user_account(user_id)
    if email:
        revoke_all_admin_sessions(email)
        log_activity(request.admin_email, 'admin_delete_user', f'Deletou {email}', request.remote_addr)
    return jsonify({'message': f'Conta {email} deletada'})


@app.route('/api/admin/job-history')
@admin_required
def admin_all_job_history():
    """Historico de todos os jobs (persistente)."""
    limit = min(int(request.args.get('limit', 100)), 500)
    return jsonify(get_all_job_history(limit))


@app.route('/api/admin/reconcile-storage', methods=['POST'])
@admin_required
def admin_reconcile_storage():
    """Recalcula storage_used_bytes de todos os usuarios a partir do disco."""
    from backend.auth import update_storage_used, _db_conn, _db_lock
    from backend.translator import _get_dir_size
    users = list_all_users()
    users_fixed = 0
    total_delta = 0

    for user in users:
        email = user['email']
        real_bytes = 0
        with _db_conn() as conn:
            rows = conn.execute(
                "SELECT job_id FROM job_history WHERE user_email = ? AND file_available = 1",
                (email,),
            ).fetchall()
        for row in rows:
            job_dir = os.path.join(JOBS_FOLDER, row['job_id'])
            if os.path.exists(job_dir):
                real_bytes += _get_dir_size(job_dir)

        quota = get_user_quota(email)
        db_bytes = quota['used_bytes']
        delta = real_bytes - db_bytes

        if abs(delta) > 1024:
            with _db_lock:
                with _db_conn() as conn:
                    conn.execute(
                        "UPDATE users SET storage_used_bytes = ? WHERE email = ?",
                        (real_bytes, email),
                    )
            log.info(f'[RECONCILE] {email}: DB={db_bytes/(1024*1024):.1f}MB, '
                     f'disk={real_bytes/(1024*1024):.1f}MB, delta={delta/(1024*1024):.1f}MB')
            users_fixed += 1
            total_delta += delta

    log_activity(request.admin_email, 'admin_reconcile',
                 f'{users_fixed} usuarios corrigidos', request.remote_addr)

    return jsonify({
        'users_fixed': users_fixed,
        'total_delta_bytes': total_delta,
        'total_delta_mb': round(total_delta / (1024 * 1024), 1),
    })


# ============================================================================
# Historico do usuario (area do cliente)
# ============================================================================

@app.route('/api/history')
@login_required
def user_history():
    """Retorna historico de jobs do usuario logado."""
    return jsonify(get_user_job_history(session['user_email']))


@app.route('/api/activity')
@login_required
def user_activity():
    """Retorna log de atividades do usuario logado."""
    limit = min(int(request.args.get('limit', 50)), 200)
    return jsonify(get_user_activity(session['user_email'], limit))


@app.route('/api/quota')
@login_required
def user_quota():
    """Retorna quota de storage do usuario logado."""
    return jsonify(get_user_quota(session['user_email']))


@app.route('/api/history/<job_id>', methods=['DELETE'])
@login_required
def delete_history_job(job_id):
    """Deleta arquivos de um job do historico (preserva metadados)."""
    if not _validate_job_id(job_id):
        return jsonify({'error': 'ID invalido'}), 400

    entry = get_job_history_entry(job_id)
    if not entry:
        return jsonify({'error': 'Job nao encontrado no historico'}), 404
    if entry['user_email'] != session['user_email']:
        return jsonify({'error': 'Acesso negado'}), 403
    from backend.translator import expire_job_files

    if not entry['file_available']:
        # Registro orfao de soft-delete antigo — remover do banco
        delete_job_history_entry(job_id)
        quota = get_user_quota(session['user_email'])
        return jsonify({
            'message': 'Registro removido',
            'freed_bytes': 0,
            'freed_mb': 0,
            'quota': quota,
        })

    freed, _ = expire_job_files(job_id)
    quota = get_user_quota(session['user_email'])

    log_activity(session['user_email'], 'delete_history',
                 f'Job {job_id} ({freed / (1024*1024):.1f} MB)', request.remote_addr)
    log.info(f'{request.remote_addr} deletou historico: {job_id} ({freed / (1024*1024):.1f} MB)')

    return jsonify({
        'message': 'Arquivos removidos',
        'freed_bytes': freed,
        'freed_mb': round(freed / (1024 * 1024), 1),
        'quota': quota,
    })


@app.route('/api/history', methods=['DELETE'])
@login_required
def delete_history_bulk():
    """Deleta arquivos de todos os jobs do usuario (ou so expirados)."""
    expired_only = request.args.get('expired_only', '').lower() in ('true', '1', 'yes')

    from backend.translator import expire_job_files
    jobs = get_user_job_history(session['user_email'], limit=200)
    total_freed = 0
    deleted_count = 0

    from datetime import datetime as _dt
    now = _dt.now().isoformat()

    for j in jobs:
        if expired_only and j['expires_at'] >= now:
            continue
        freed, _ = expire_job_files(j['job_id'])
        total_freed += freed
        deleted_count += 1

    quota = get_user_quota(session['user_email'])

    log_activity(session['user_email'], 'delete_history_bulk',
                 f'{deleted_count} jobs ({total_freed / (1024*1024):.1f} MB)',
                 request.remote_addr)
    log.info(f'{request.remote_addr} bulk delete: {deleted_count} jobs '
             f'({total_freed / (1024*1024):.1f} MB)')

    return jsonify({
        'message': f'{deleted_count} jobs limpos',
        'deleted_count': deleted_count,
        'freed_bytes': total_freed,
        'freed_mb': round(total_freed / (1024 * 1024), 1),
        'quota': quota,
    })


# ============================================================================
# WebSocket
# ============================================================================

@socketio.on('connect')
def ws_connect():
    log.debug(f'WS conectado: {request.remote_addr}')


@socketio.on('disconnect')
def ws_disconnect():
    log.debug(f'WS desconectado: {request.remote_addr}')


@socketio.on('join_job')
def ws_join_job(data):
    if 'user_email' not in session:
        return
    job_id = data.get('job_id', '')
    if not _validate_job_id(job_id):
        return
    job = get_job(job_id)
    if not job or job.user_email != session['user_email']:
        return
    join_room(job_id)
    log.debug(f'WS join_job: {job_id} ({request.remote_addr})')
    socketio.emit('translation_progress', job.to_dict(), room=job_id)


# ============================================================================
# Frontend SPA (React build)
# ============================================================================

@app.route('/')
def serve_index():
    index = os.path.join(STATIC_FOLDER, 'index.html')
    if os.path.exists(index):
        return send_from_directory(STATIC_FOLDER, 'index.html')
    return (
        '<html><body style="font-family:sans-serif;padding:40px;text-align:center">'
        '<h1>Traducao</h1>'
        '<p>Frontend nao compilado. Execute: <code>cd frontend &amp;&amp; npm run build</code></p>'
        '</body></html>'
    )


@app.route('/<path:path>')
def serve_static(path):
    full = os.path.join(STATIC_FOLDER, path)
    if os.path.exists(full):
        return send_from_directory(STATIC_FOLDER, path)
    index = os.path.join(STATIC_FOLDER, 'index.html')
    if os.path.exists(index):
        return send_from_directory(STATIC_FOLDER, 'index.html')
    return jsonify({'error': 'Not found'}), 404


# ============================================================================
# Entry-point de desenvolvimento
# ============================================================================

if __name__ == '__main__':
    cleanup_old_jobs(max_age_hours=168)  # 7 dias
    cleanup_expired_sessions()
    # Limpar arquivos de jobs expirados (preserva historico)
    from backend.translator import expire_job_files as _expire
    for _expired_id in cleanup_expired_jobs():
        _expire(_expired_id)
    log.info('Servidor iniciando em http://localhost:5000')
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
