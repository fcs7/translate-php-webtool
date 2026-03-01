"""
Servico de traducao — integra com translate.py do projeto.
Importa funcoes do script existente e adiciona progresso via WebSocket.
"""

import io
import os
import shutil
import subprocess
import tarfile
import time
import zipfile
import uuid
import threading
from datetime import datetime

from concurrent.futures import ThreadPoolExecutor, as_completed

import backend.translate as trans_engine

from backend.config import JOBS_FOLDER, DEFAULT_DELAY, log
from backend.engine import get_engine
from backend.auth import save_job_db, get_jobs_db, get_job_db, delete_job_db, update_storage_used

BATCH_SIZE = 100
MAX_PARALLEL_FILES = 4

# Lock para proteger estado compartilhado do job durante traducao paralela
_progress_lock = threading.Lock()


# ============================================================================
# Model — Job de traducao
# ============================================================================

class TranslationJob:
    """Representa um job de traducao com estado e progresso."""

    def __init__(self, job_id, input_dir, output_dir, delay=DEFAULT_DELAY, user_email=''):
        self.job_id = job_id
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.delay = delay
        self.user_email = user_email

        # Estado
        self.status = 'pending'
        self.progress = 0
        self.current_file = ''
        self.total_files = 0
        self.files_done = 0
        self.total_strings = 0
        self.translated_strings = 0
        self.errors = []
        self.validation = None
        self.output_zip = None
        self.output_tar = None
        self.file_size_bytes = 0

        # Timestamps
        self.created_at = datetime.now().isoformat()
        self.started_at = None
        self.finished_at = None

        # Controle interno
        self._cancel_flag = False

    def to_dict(self):
        return {
            'job_id': self.job_id,
            'status': self.status,
            'progress': self.progress,
            'current_file': self.current_file,
            'total_files': self.total_files,
            'files_done': self.files_done,
            'total_strings': self.total_strings,
            'translated_strings': self.translated_strings,
            'errors': self.errors[-10:],
            'created_at': self.created_at,
            'started_at': self.started_at,
            'finished_at': self.finished_at,
            'has_output': self.output_zip is not None,
            'has_voipnow': self.output_tar is not None,
            'validation': self.validation,
            'user_email': self.user_email,
            'file_size_bytes': self.file_size_bytes,
        }

    def cancel(self):
        self._cancel_flag = True


# ============================================================================
# Registro global de jobs (em memoria)
# ============================================================================

_jobs = {}
_jobs_lock = threading.Lock()


def _get(job_id):
    with _jobs_lock:
        return _jobs.get(job_id)


def _put(job):
    with _jobs_lock:
        _jobs[job.job_id] = job


def _pop(job_id):
    with _jobs_lock:
        return _jobs.pop(job_id, None)


def count_running_jobs():
    """Conta quantos jobs estao em execucao."""
    with _jobs_lock:
        return sum(1 for j in _jobs.values() if j.status == 'running')


# ============================================================================
# Helpers de extracao (ZIP, RAR, TAR)
# ============================================================================

ALLOWED_EXTENSIONS = ('.zip', '.rar', '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2')


def _safe_zip_extract(zf, extract_to):
    """Extrai ZIP validando cada membro contra path traversal (ZIP Slip)."""
    target = os.path.realpath(extract_to)
    for member in zf.namelist():
        member_path = os.path.realpath(os.path.join(target, member))
        if not member_path.startswith(target + os.sep) and member_path != target:
            raise ValueError(f"Path traversal detectado: {member}")
    zf.extractall(extract_to)


def _extract_archive(archive_path, extract_to):
    """Extrai ZIP, RAR ou TAR e retorna o diretorio com arquivos .php."""
    lower = archive_path.lower()
    basename = os.path.basename(archive_path)

    if lower.endswith('.zip'):
        log.info(f'Extraindo ZIP: {basename}')
        with zipfile.ZipFile(archive_path, 'r') as zf:
            _safe_zip_extract(zf, extract_to)

    elif lower.endswith('.rar'):
        log.info(f'Extraindo RAR: {basename}')
        subprocess.run(
            ['unrar', 'x', '-o+', archive_path, extract_to],
            check=True, capture_output=True,
        )

    elif lower.endswith(('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2')):
        log.info(f'Extraindo TAR: {basename}')
        with tarfile.open(archive_path, 'r:*') as tf:
            tf.extractall(extract_to, filter='data')

    else:
        raise ValueError(f"Formato nao suportado: {basename}")

    # Encontrar diretorio com PHPs
    for dirpath, _, filenames in os.walk(extract_to):
        php_count = sum(1 for f in filenames if f.endswith('.php'))
        if php_count > 0:
            log.info(f'Encontrados {php_count} arquivos PHP em {os.path.relpath(dirpath, extract_to)}')
            return dirpath

    log.warning(f'Nenhum arquivo PHP encontrado no arquivo {basename}')
    return extract_to


def _create_zip(source_dir, zip_path):
    """Compacta diretorio de saida em ZIP."""
    file_count = 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _, filenames in os.walk(source_dir):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                zf.write(full, os.path.relpath(full, source_dir))
                file_count += 1
    size_kb = os.path.getsize(zip_path) / 1024
    log.info(f'ZIP criado: {file_count} arquivos, {size_kb:.1f} KB')


def _create_voipnow_tar(source_dir, tar_path):
    """Cria tar.gz no formato VoipNow language pack."""
    meta_content = "ISO: pt_br\nLanguage: Portuguese\nCharset: UTF-8\nVersion: 5.7.0\n"
    file_count = 0

    with tarfile.open(tar_path, 'w:gz') as tar:
        # Adicionar meta
        meta_info = tarfile.TarInfo(name='language/meta')
        meta_bytes = meta_content.encode('utf-8')
        meta_info.size = len(meta_bytes)
        tar.addfile(meta_info, io.BytesIO(meta_bytes))

        # Adicionar PHPs em language/pt_br/
        for dirpath, _, filenames in os.walk(source_dir):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                arcname = 'language/pt_br/' + os.path.relpath(full, source_dir)
                tar.add(full, arcname=arcname)
                file_count += 1

    size_kb = os.path.getsize(tar_path) / 1024
    log.info(f'VoipNow TAR criado: {file_count} arquivos, {size_kb:.1f} KB')


def _get_dir_size(path):
    """Retorna tamanho total de um diretorio em bytes."""
    total = 0
    try:
        for dirpath, _, filenames in os.walk(path):
            for fname in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, fname))
                except OSError as e:
                    log.warning(f'[DIR_SIZE] Nao conseguiu ler {os.path.join(dirpath, fname)}: {e}')
    except OSError as e:
        log.error(f'[DIR_SIZE] Erro ao percorrer {path}: {e}')
    return total


# ============================================================================
# Contagem de strings
# ============================================================================

def _count_strings(file_path):
    """Conta $msg_arr em um arquivo PHP."""
    count = 0
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                s = line.rstrip('\n')
                if trans_engine.SINGLE_QUOTE_RE.match(s) or \
                   trans_engine.DOUBLE_QUOTE_RE.match(s):
                    count += 1
    except Exception:
        pass
    return count


# ============================================================================
# Traducao de arquivo individual (com progresso)
# ============================================================================

def _translate_file(src_path, dst_path, delay, job, socketio=None):
    """
    Traduz um arquivo PHP usando batch translation (3-pass).
    Pass 1: coleta strings traduziveis. Pass 2: traduz em lotes. Pass 3: grava arquivo.
    """
    rel = os.path.relpath(src_path, job.input_dir)

    try:
        with open(src_path, 'r', encoding='utf-8') as f:
            src_lines = f.readlines()
    except Exception as e:
        log.error(f'[{job.job_id}] Erro ao ler {rel}: {e}')
        with _progress_lock:
            job.errors.append(f"Erro leitura: {rel}: {e}")
        return 0

    total_lines = len(src_lines)

    # Resume
    start_line = 0
    if os.path.exists(dst_path):
        try:
            with open(dst_path, 'r', encoding='utf-8') as f:
                start_line = len(f.readlines())
            if start_line >= total_lines:
                log.debug(f'[{job.job_id}] Pulando (completo): {rel}')
                return 0
            log.info(f'[{job.job_id}] Resumindo {rel} da linha {start_line + 1}/{total_lines}')
        except Exception:
            pass
    else:
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)

    mode = 'a' if start_line > 0 else 'w'
    count = 0

    # --- Pass 1: coletar strings traduziveis ---
    entries = []  # (idx_in_output, text_prepared, ph_map, prefix, suffix, qc)
    output_lines = []

    for i in range(start_line, total_lines):
        line = src_lines[i]
        stripped = line.rstrip('\n')

        m = trans_engine.SINGLE_QUOTE_RE.match(stripped)
        qc = "'"
        if not m:
            m = trans_engine.DOUBLE_QUOTE_RE.match(stripped)
            qc = '"'

        if m:
            prefix, raw_value, suffix = m.group(1), m.group(2), m.group(3)
            text = trans_engine.prepare_for_translation(raw_value, qc)
            text, ph_map = trans_engine.protect_placeholders(text)
            entries.append((len(output_lines), text, ph_map, prefix, suffix, qc))
            output_lines.append(line)  # fallback: linha original preservada se cancel
        else:
            output_lines.append(line)

    # --- Pass 2: traduzir em batches ---
    engine = get_engine()

    try:
        for batch_start in range(0, len(entries), BATCH_SIZE):
            if job._cancel_flag:
                log.info(f'[{job.job_id}] Cancelado durante {rel} (batch {batch_start // BATCH_SIZE})')
                break

            batch_entries = entries[batch_start:batch_start + BATCH_SIZE]
            batch_texts = [e[1] for e in batch_entries]

            translations = engine.translate_batch(batch_texts)

            for entry, translated in zip(batch_entries, translations):
                idx, _text, ph_map, prefix, suffix, qc = entry
                translated = trans_engine.restore_placeholders(translated, ph_map)
                translated = trans_engine.re_escape(translated, qc)
                output_lines[idx] = prefix + translated + suffix + '\n'
                count += 1
                with _progress_lock:
                    job.translated_strings += 1

            # Progresso via WebSocket a cada batch
            if socketio and job.total_strings > 0:
                with _progress_lock:
                    job.progress = int((job.translated_strings / job.total_strings) * 100)
                socketio.emit('translation_progress', job.to_dict(), room=job.job_id)

            time.sleep(delay)

    except Exception as e:
        log.error(f'[{job.job_id}] Erro em {rel} batch: {e}')
        with _progress_lock:
            job.errors.append(f"Erro: {rel}: {e}")

    # --- Pass 3: escrever arquivo ---
    try:
        with open(dst_path, mode, encoding='utf-8') as out:
            for line in output_lines:
                out.write(line)
            out.flush()
    except Exception as e:
        log.error(f'[{job.job_id}] Erro ao escrever {rel}: {e}')
        with _progress_lock:
            job.errors.append(f"Erro escrita: {rel}: {e}")

    log.info(f'[{job.job_id}] {rel}: {count} strings traduzidas (batch)')
    return count


# ============================================================================
# Runner — executa traducao em background thread
# ============================================================================

def _run(job, socketio):
    """Thread principal de traducao."""
    log.info(f'[{job.job_id}] Iniciando traducao (delay={job.delay}s)')
    job.status = 'running'
    job.started_at = datetime.now().isoformat()
    socketio.emit('translation_progress', job.to_dict(), room=job.job_id)

    try:
        log.debug(f'[{job.job_id}] Inicializando engine de traducao...')
        get_engine()  # Garante que a engine esta inicializada

        # Coletar arquivos PHP
        tasks = []
        for dirpath, dirnames, filenames in os.walk(job.input_dir):
            dirnames.sort()
            for fname in sorted(filenames):
                if not fname.endswith('.php'):
                    continue
                src = os.path.join(dirpath, fname)
                rel = os.path.relpath(src, job.input_dir)
                dst = os.path.join(job.output_dir, rel)
                tasks.append((src, dst, rel, _count_strings(src)))

        job.total_files = len(tasks)
        job.total_strings = sum(t[3] for t in tasks)
        log.info(f'[{job.job_id}] {job.total_files} arquivos, {job.total_strings} strings')
        socketio.emit('translation_progress', job.to_dict(), room=job.job_id)

        if not tasks:
            log.error(f'[{job.job_id}] Nenhum arquivo PHP encontrado')
            job.errors.append("Nenhum arquivo PHP encontrado no arquivo enviado")
            job.status = 'failed'
            job.finished_at = datetime.now().isoformat()
            save_job_db(job.to_dict())
            socketio.emit('translation_error', job.to_dict(), room=job.job_id)
            return

        # Traducao paralela de arquivos (MAX_PARALLEL_FILES workers)
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_FILES) as pool:
            futures = {}
            for idx, (src, dst, rel, _) in enumerate(tasks):
                if job._cancel_flag:
                    break
                future = pool.submit(_translate_file, src, dst, job.delay, job, socketio)
                futures[future] = (idx, rel)

            cancelled = False
            for future in as_completed(futures):
                idx, rel = futures[future]
                try:
                    future.result()
                except Exception as e:
                    log.error(f'[{job.job_id}] Erro thread {rel}: {e}')
                    with _progress_lock:
                        job.errors.append(f"Erro thread: {rel}: {e}")

                with _progress_lock:
                    job.files_done += 1
                    job.current_file = rel
                    if job.total_strings > 0:
                        job.progress = int((job.translated_strings / job.total_strings) * 100)
                socketio.emit('translation_progress', job.to_dict(), room=job.job_id)

                if job._cancel_flag:
                    cancelled = True
                    break

            if cancelled:
                job.status = 'cancelled'
                job.finished_at = datetime.now().isoformat()
                log.info(f'[{job.job_id}] Cancelado pelo usuario ({job.files_done}/{job.total_files} arquivos)')
                save_job_db(job.to_dict())
                socketio.emit('translation_progress', job.to_dict(), room=job.job_id)
                try:
                    from backend.auth import save_job_history
                    save_job_history(job.to_dict())
                except Exception as e:
                    log.error(f'[{job.job_id}] Erro ao salvar historico de job cancelado: {e}')
                return

        # Finalizar
        job.files_done = job.total_files
        job.progress = 100
        job.current_file = ''

        # Validar
        log.info(f'[{job.job_id}] Validando traducao...')
        try:
            stats, issues = trans_engine.validate_translation(job.input_dir, job.output_dir)
            job.validation = {'stats': stats, 'issues': issues[:20]}
            log.info(f'[{job.job_id}] Validacao: {stats["success"]} OK, '
                     f'{stats["untranslated"]} nao traduzidas, '
                     f'{stats["missing_placeholders"]} placeholders perdidos')
        except Exception as e:
            log.error(f'[{job.job_id}] Erro na validacao: {e}')
            job.validation = {'error': 'Falha na validacao da traducao'}

        # ZIP de saida
        zip_path = os.path.join(JOBS_FOLDER, job.job_id, 'output.zip')
        log.info(f'[{job.job_id}] Criando ZIP de saida...')
        _create_zip(job.output_dir, zip_path)
        job.output_zip = zip_path

        # VoipNow TAR
        tar_path = os.path.join(JOBS_FOLDER, job.job_id, 'voipnow.tar.gz')
        log.info(f'[{job.job_id}] Criando VoipNow TAR...')
        _create_voipnow_tar(job.output_dir, tar_path)
        job.output_tar = tar_path

        job.status = 'completed'
        job.finished_at = datetime.now().isoformat()

        # Calcular tamanho e atualizar quota
        job.file_size_bytes = _get_dir_size(os.path.join(JOBS_FOLDER, job.job_id))
        if job.user_email:
            update_storage_used(job.user_email, job.file_size_bytes)

        # Persistir no DB
        save_job_db(job.to_dict())

        elapsed = (datetime.fromisoformat(job.finished_at) -
                   datetime.fromisoformat(job.started_at)).total_seconds()
        cache_stats = get_engine().cache.get_stats()
        log.info(f'[{job.job_id}] CONCLUIDO em {elapsed:.1f}s — '
                 f'{job.translated_strings} strings, '
                 f'{cache_stats["l1_size"]} unicas (cache L1, '
                 f'hit rate {cache_stats["hit_rate_total"]})')

        socketio.emit('translation_complete', job.to_dict(), room=job.job_id)

        # Persistir no historico
        try:
            from backend.auth import save_job_history
            save_job_history(job.to_dict())
        except Exception as e:
            log.debug(f'[{job.job_id}] Erro ao salvar historico: {e}')

    except Exception as e:
        job.status = 'failed'
        job.finished_at = datetime.now().isoformat()
        job.errors.append(f"Erro fatal: {str(e)}")
        log.error(f'[{job.job_id}] FALHA FATAL: {e}', exc_info=True)
        save_job_db(job.to_dict())
        socketio.emit('translation_error', job.to_dict(), room=job.job_id)

        # Persistir falha no historico
        try:
            from backend.auth import save_job_history
            save_job_history(job.to_dict())
        except Exception as e:
            log.debug(f'[{job.job_id}] Erro ao salvar historico: {e}')


# ============================================================================
# API publica do servico
# ============================================================================

def start_translation(archive_path, delay, socketio, user_email=''):
    """Inicia novo job a partir de arquivo compactado. Retorna job_id."""
    job_id = str(uuid.uuid4())[:8]
    job_dir = os.path.join(JOBS_FOLDER, job_id)
    input_dir = os.path.join(job_dir, 'input')
    output_dir = os.path.join(job_dir, 'output')

    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    log.info(f'[{job_id}] Extraindo arquivo...')
    php_dir = _extract_archive(archive_path, input_dir)

    job = TranslationJob(job_id, php_dir, output_dir, delay, user_email)
    _put(job)

    # Persistir estado inicial no DB
    save_job_db(job.to_dict())

    threading.Thread(target=_run, args=(job, socketio), daemon=True).start()
    log.info(f'[{job_id}] Thread de traducao iniciada')
    return job_id


def start_translation_raw(php_dir, delay, socketio, user_email=''):
    """Inicia novo job a partir de arquivos PHP avulsos (sem extracao). Retorna job_id."""
    job_id = str(uuid.uuid4())[:8]
    job_dir = os.path.join(JOBS_FOLDER, job_id)
    input_dir = os.path.join(job_dir, 'input')
    output_dir = os.path.join(job_dir, 'output')

    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    # Mover arquivos PHP para o diretorio do job
    for dirpath, dirnames, filenames in os.walk(php_dir):
        for fname in filenames:
            src = os.path.join(dirpath, fname)
            rel = os.path.relpath(src, php_dir)
            dst = os.path.join(input_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)

    # Limpar diretorio temporario original
    shutil.rmtree(php_dir, ignore_errors=True)

    log.info(f'[{job_id}] Arquivos PHP movidos para job (sem extracao)')

    job = TranslationJob(job_id, input_dir, output_dir, delay, user_email)
    _put(job)

    threading.Thread(target=_run, args=(job, socketio), daemon=True).start()
    log.info(f'[{job_id}] Thread de traducao iniciada')
    return job_id


def get_job(job_id):
    return _get(job_id)


def _resolve_job_owner(job_id, include_history=False):
    """Busca user_email e file_size_bytes do job (memoria > DB > historico).
    Retorna (user_email, file_size_bytes) ou (None, 0) se nao encontrou.
    """
    job = _get(job_id)
    if job and job.user_email:
        return job.user_email, job.file_size_bytes

    db_job = get_job_db(job_id)
    if db_job and db_job.get('user_email'):
        return db_job['user_email'], db_job.get('file_size_bytes', 0)

    if include_history:
        from backend.auth import get_job_history_entry
        history = get_job_history_entry(job_id)
        if history and history.get('user_email'):
            return history['user_email'], history.get('file_size_bytes', 0)

    return None, 0


def delete_job(job_id):
    user_email, file_size = _resolve_job_owner(job_id)
    _pop(job_id)  # remover da memoria

    job_dir = os.path.join(JOBS_FOLDER, job_id)
    if os.path.exists(job_dir):
        shutil.rmtree(job_dir, ignore_errors=True)

    delete_job_db(job_id)

    # Devolver quota
    if user_email and file_size > 0:
        update_storage_used(user_email, -file_size)

    log.info(f'[{job_id}] Job removido e arquivos limpos')
    return True


def expire_job_files(job_id):
    """Remove arquivos e registro de historico de um job.
    - Remove pasta jobs/{job_id}/ do disco
    - Atualiza storage_used_bytes do usuario (negativo)
    - Deleta registro do job_history
    - Remove da tabela jobs ativa
    Retorna (freed_bytes, user_email) ou (0, None) se nao encontrou.
    """
    from backend.auth import delete_job_history_entry

    user_email, file_size = _resolve_job_owner(job_id, include_history=True)

    if not user_email:
        log.warning(f'[{job_id}] expire_job_files: job nao encontrado em nenhuma fonte')
        return 0, None

    # Remover arquivos do disco
    job_dir = os.path.join(JOBS_FOLDER, job_id)
    freed_bytes = 0
    if os.path.exists(job_dir):
        freed_bytes = _get_dir_size(job_dir)
        try:
            shutil.rmtree(job_dir)
        except OSError as e:
            log.error(f'[{job_id}] Falha ao remover diretorio {job_dir}: {e}')
            if os.path.exists(job_dir):
                freed_bytes = 0  # nao alegar espaco liberado se falhou

    # Se nao tinha tamanho no disco, usar tamanho do DB
    if freed_bytes == 0 and file_size > 0:
        freed_bytes = file_size

    # Remover registro do historico
    if not delete_job_history_entry(job_id):
        log.error(f'[{job_id}] Falha ao deletar historico — registro orfao pode persistir')

    # Remover da tabela jobs ativa (se existir)
    delete_job_db(job_id)

    # Remover da memoria
    _pop(job_id)

    # Devolver quota
    if user_email and freed_bytes > 0:
        update_storage_used(user_email, -freed_bytes)

    log.info(f'[{job_id}] Arquivos expirados ({freed_bytes / (1024*1024):.1f} MB liberados)')
    return freed_bytes, user_email


def list_jobs(user_email=None):
    """Combina jobs em memória (prioridade) + DB (historico)."""
    merged = {}

    # DB primeiro (historico)
    if user_email:
        for jd in get_jobs_db(user_email):
            merged[jd['job_id']] = jd

    # Memoria sobrescreve (dados em tempo real)
    with _jobs_lock:
        for j in _jobs.values():
            if user_email and j.user_email != user_email:
                continue
            merged[j.job_id] = j.to_dict()

    # Ordenar por created_at descendente
    result = sorted(merged.values(), key=lambda x: x.get('created_at', ''), reverse=True)
    return result


def cleanup_old_jobs(max_age_hours=24):
    """Remove jobs finalizados com mais de X horas."""
    now = datetime.now()
    to_delete = []
    with _jobs_lock:
        for jid, job in _jobs.items():
            created = datetime.fromisoformat(job.created_at)
            if (now - created).total_seconds() / 3600 > max_age_hours \
               and job.status in ('completed', 'failed', 'cancelled'):
                to_delete.append(jid)
    for jid in to_delete:
        delete_job(jid)
    if to_delete:
        log.info(f'Cleanup: {len(to_delete)} jobs antigos removidos')
