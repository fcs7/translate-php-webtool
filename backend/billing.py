"""Modulo de billing — Asaas Pix + gestao de planos."""

import hmac
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta

from backend.config import (
    ASAAS_API_KEY, ASAAS_API_URL, ASAAS_WEBHOOK_TOKEN,
    PLAN_STORAGE_LIMITS, PLAN_PRICES, PLAN_DURATION_DAYS, log,
)
from backend.auth import _db_lock, _db_conn, log_activity


# ============================================================================
# DB — tabelas de billing
# ============================================================================

def init_billing_db():
    """Cria tabelas de billing e migra colunas de plano na tabela users."""
    with _db_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_email TEXT NOT NULL,
                asaas_payment_id TEXT UNIQUE NOT NULL,
                asaas_customer_id TEXT NOT NULL,
                plan TEXT NOT NULL,
                value REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                pix_payload TEXT,
                pix_qrcode_base64 TEXT,
                pix_expiration TEXT,
                created_at TEXT NOT NULL,
                confirmed_at TEXT,
                expires_at TEXT
            );

            CREATE TABLE IF NOT EXISTS webhook_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                event_type TEXT NOT NULL,
                payment_id TEXT,
                processed_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_payments_email ON payments(user_email);
            CREATE INDEX IF NOT EXISTS idx_payments_asaas ON payments(asaas_payment_id);
            CREATE INDEX IF NOT EXISTS idx_webhook_event_id ON webhook_events(event_id);
        """)

        # Migracoes: colunas de plano na tabela users
        for col, definition in [
            ('asaas_customer_id', 'TEXT'),
            ('plan', "TEXT DEFAULT 'free'"),
            ('plan_status', "TEXT DEFAULT 'active'"),
            ('plan_expires_at', 'TEXT'),
            ('plan_updated_at', 'TEXT'),
        ]:
            try:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN %s %s" % (col, definition)
                )
            except Exception:
                pass  # coluna ja existe

    log.info('[BILLING] Tabelas de billing inicializadas')


# ============================================================================
# Asaas API (stdlib — urllib.request)
# ============================================================================

def _asaas_request(method, endpoint, data=None):
    """Faz request para a API Asaas. Retorna dict com resposta JSON."""
    url = '%s%s' % (ASAAS_API_URL.rstrip('/'), endpoint)
    headers = {
        'Content-Type': 'application/json',
        'access_token': ASAAS_API_KEY,
    }

    body = json.dumps(data).encode('utf-8') if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        log.error('[ASAAS] HTTP %d %s %s: %s', e.code, method, endpoint, error_body)
        raise RuntimeError('Erro na API Asaas: HTTP %d' % e.code)
    except urllib.error.URLError as e:
        log.error('[ASAAS] Erro de conexao %s %s: %s', method, endpoint, e)
        raise RuntimeError('Erro de conexao com Asaas: %s' % e.reason)


def get_or_create_asaas_customer(email):
    """Retorna asaas_customer_id para o usuario. Cria no Asaas se nao existir."""
    email = email.strip().lower()

    with _db_lock:
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT asaas_customer_id FROM users WHERE email = ?",
                (email,),
            ).fetchone()
            if row and row['asaas_customer_id']:
                return row['asaas_customer_id']

    # Criar customer no Asaas
    result = _asaas_request('POST', '/v3/customers', {
        'name': email.split('@')[0],
        'email': email,
    })
    customer_id = result['id']

    with _db_lock:
        with _db_conn() as conn:
            conn.execute(
                "UPDATE users SET asaas_customer_id = ? WHERE email = ?",
                (customer_id, email),
            )

    log.info('[BILLING] Customer Asaas criado: %s -> %s', email, customer_id)
    return customer_id


def create_pix_payment(email, plan):
    """Cria cobranca Pix no Asaas e retorna dados do QR Code."""
    email = email.strip().lower()

    if plan not in PLAN_PRICES:
        raise ValueError('Plano invalido: %s' % plan)

    value = PLAN_PRICES[plan]
    customer_id = get_or_create_asaas_customer(email)
    now = datetime.now()
    due_date = (now + timedelta(days=3)).strftime('%Y-%m-%d')

    # Criar cobranca
    payment = _asaas_request('POST', '/v3/payments', {
        'customer': customer_id,
        'billingType': 'PIX',
        'value': value,
        'dueDate': due_date,
        'description': 'Trans-Script Plano %s — %d dias' % (
            plan.capitalize(), PLAN_DURATION_DAYS
        ),
    })

    payment_id = payment['id']

    # Obter QR Code
    qrcode_data = _asaas_request('GET', '/v3/payments/%s/pixQrCode' % payment_id)

    # Salvar no DB
    with _db_lock:
        with _db_conn() as conn:
            conn.execute(
                """INSERT INTO payments
                   (user_email, asaas_payment_id, asaas_customer_id, plan, value,
                    status, pix_payload, pix_qrcode_base64, pix_expiration, created_at)
                   VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?)""",
                (
                    email, payment_id, customer_id, plan, value,
                    qrcode_data.get('payload'),
                    qrcode_data.get('encodedImage'),
                    qrcode_data.get('expirationDate'),
                    now.isoformat(),
                ),
            )

    log.info('[BILLING] Cobranca Pix criada: %s plan=%s payment=%s',
             email, plan, payment_id)

    return {
        'payment_id': payment_id,
        'plan': plan,
        'value': value,
        'qrcode_base64': qrcode_data.get('encodedImage'),
        'pix_payload': qrcode_data.get('payload'),
        'expiration': qrcode_data.get('expirationDate'),
    }


def get_payment_status(asaas_payment_id):
    """Consulta status de pagamento no DB local."""
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT asaas_payment_id, user_email, plan, value, status, "
            "created_at, confirmed_at, expires_at "
            "FROM payments WHERE asaas_payment_id = ?",
            (asaas_payment_id,),
        ).fetchone()
        return dict(row) if row else None


# ============================================================================
# Plan management
# ============================================================================

def get_user_plan(email):
    """Retorna info do plano atual do usuario."""
    email = email.strip().lower()
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT plan, plan_status, plan_expires_at, plan_updated_at "
            "FROM users WHERE email = ?",
            (email,),
        ).fetchone()

    if not row:
        return {
            'plan': 'free', 'status': 'active',
            'expires_at': None, 'days_remaining': None,
            'storage_limit_mb': round(PLAN_STORAGE_LIMITS['free'] / (1024 * 1024)),
        }

    plan = row['plan'] or 'free'
    expires_at = row['plan_expires_at']
    days_remaining = None

    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at)
            delta = exp - datetime.now()
            days_remaining = max(0, delta.days)
        except (ValueError, TypeError):
            pass

    limit_bytes = PLAN_STORAGE_LIMITS.get(plan, PLAN_STORAGE_LIMITS['free'])

    return {
        'plan': plan,
        'status': row['plan_status'] or 'active',
        'expires_at': expires_at,
        'days_remaining': days_remaining,
        'storage_limit_mb': round(limit_bytes / (1024 * 1024)),
    }


def activate_plan(email, plan, payment_id):
    """Ativa plano para o usuario por PLAN_DURATION_DAYS dias."""
    email = email.strip().lower()
    now = datetime.now()
    expires_at = (now + timedelta(days=PLAN_DURATION_DAYS)).isoformat()
    limit_bytes = PLAN_STORAGE_LIMITS.get(plan, PLAN_STORAGE_LIMITS['free'])

    with _db_lock:
        with _db_conn() as conn:
            conn.execute(
                """UPDATE users SET
                       plan = ?, plan_status = 'active',
                       plan_expires_at = ?, plan_updated_at = ?,
                       storage_limit_bytes = ?
                   WHERE email = ?""",
                (plan, expires_at, now.isoformat(), limit_bytes, email),
            )

            # Atualizar payment
            conn.execute(
                """UPDATE payments SET
                       status = 'RECEIVED', confirmed_at = ?, expires_at = ?
                   WHERE asaas_payment_id = ?""",
                (now.isoformat(), expires_at, payment_id),
            )

    log_activity(email, 'plan_activated',
                 '%s — %d dias' % (plan.capitalize(), PLAN_DURATION_DAYS))
    log.info('[BILLING] Plano ativado: %s plan=%s expires=%s', email, plan, expires_at)


def downgrade_to_free(email):
    """Rebaixa usuario para plano free."""
    email = email.strip().lower()
    now = datetime.now().isoformat()
    free_limit = PLAN_STORAGE_LIMITS['free']

    with _db_lock:
        with _db_conn() as conn:
            conn.execute(
                """UPDATE users SET
                       plan = 'free', plan_status = 'active',
                       plan_expires_at = NULL, plan_updated_at = ?,
                       storage_limit_bytes = ?
                   WHERE email = ?""",
                (now, free_limit, email),
            )

    log.info('[BILLING] Downgrade para free: %s', email)


def check_plan_expiry():
    """Verifica e expira planos vencidos. Retorna quantidade expirada."""
    now = datetime.now()
    now_iso = now.isoformat()
    free_limit = PLAN_STORAGE_LIMITS['free']

    with _db_lock:
        with _db_conn() as conn:
            # Buscar usuarios para logging antes do batch update
            rows = conn.execute(
                "SELECT email, plan FROM users "
                "WHERE plan != 'free' AND plan_expires_at IS NOT NULL "
                "AND plan_expires_at < ?",
                (now_iso,),
            ).fetchall()

            if not rows:
                return 0

            # Batch update — single write para todos os expirados
            conn.execute(
                """UPDATE users SET
                       plan = 'free', plan_status = 'active',
                       plan_expires_at = NULL, plan_updated_at = ?,
                       storage_limit_bytes = ?
                   WHERE plan != 'free' AND plan_expires_at IS NOT NULL
                   AND plan_expires_at < ?""",
                (now_iso, free_limit, now_iso),
            )

    # Logging fora do lock
    for row in rows:
        log_activity(row['email'], 'plan_expired',
                     '%s expirado' % row['plan'].capitalize())

    count = len(rows)
    log.info('[BILLING] %d plano(s) expirado(s) rebaixado(s) para free', count)
    return count


# ============================================================================
# Webhook helpers
# ============================================================================

def is_webhook_processed(event_id):
    """Verifica se evento ja foi processado (idempotencia)."""
    with _db_lock:
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT id FROM webhook_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            return row is not None


def mark_webhook_processed(event_id, event_type, payment_id):
    """Marca evento como processado."""
    now = datetime.now().isoformat()
    with _db_lock:
        with _db_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO webhook_events "
                "(event_id, event_type, payment_id, processed_at) "
                "VALUES (?, ?, ?, ?)",
                (event_id, event_type, payment_id, now),
            )


def process_payment_received(payment_data):
    """Processa webhook PAYMENT_RECEIVED/CONFIRMED."""
    asaas_payment_id = payment_data.get('id')
    if not asaas_payment_id:
        log.warning('[BILLING] Webhook sem payment id')
        return

    # Buscar payment no DB local
    with _db_lock:
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT user_email, plan FROM payments WHERE asaas_payment_id = ?",
                (asaas_payment_id,),
            ).fetchone()

    if not row:
        log.warning('[BILLING] Payment nao encontrado localmente: %s',
                     asaas_payment_id)
        return

    activate_plan(row['user_email'], row['plan'], asaas_payment_id)


def process_payment_refunded(payment_data):
    """Processa webhook PAYMENT_REFUNDED."""
    asaas_payment_id = payment_data.get('id')
    if not asaas_payment_id:
        return

    row = None
    with _db_lock:
        with _db_conn() as conn:
            row = conn.execute(
                "SELECT user_email FROM payments WHERE asaas_payment_id = ?",
                (asaas_payment_id,),
            ).fetchone()

            if row:
                conn.execute(
                    "UPDATE payments SET status = 'REFUNDED' WHERE asaas_payment_id = ?",
                    (asaas_payment_id,),
                )

    if row:
        downgrade_to_free(row['user_email'])
        log_activity(row['user_email'], 'plan_refunded',
                     'Pagamento estornado')
        log.info('[BILLING] Estorno processado: %s', row['user_email'])


def validate_webhook_token(token):
    """Valida token do webhook Asaas (constant-time)."""
    if not ASAAS_WEBHOOK_TOKEN:
        log.warning('[BILLING] ASAAS_WEBHOOK_TOKEN nao configurado')
        return False
    return hmac.compare_digest(token, ASAAS_WEBHOOK_TOKEN)


# ============================================================================
# Admin stats
# ============================================================================

def get_billing_stats():
    """Retorna estatisticas de billing para o admin."""
    now = datetime.now()
    month_start = now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    ).isoformat()

    with _db_lock:
        with _db_conn() as conn:
            # Assinantes por plano
            plan_counts = {}
            for row in conn.execute(
                "SELECT COALESCE(plan, 'free') as plan, COUNT(*) as cnt "
                "FROM users GROUP BY plan"
            ).fetchall():
                plan_counts[row['plan']] = row['cnt']

            # Pagamentos recentes (ultimos 30)
            recent_payments = []
            for row in conn.execute(
                "SELECT user_email, plan, value, status, created_at, confirmed_at "
                "FROM payments ORDER BY created_at DESC LIMIT 30"
            ).fetchall():
                recent_payments.append(dict(row))

            # Receita total (apenas confirmados)
            revenue = conn.execute(
                "SELECT COALESCE(SUM(value), 0) as total FROM payments "
                "WHERE status IN ('RECEIVED', 'CONFIRMED')"
            ).fetchone()

            monthly_revenue = conn.execute(
                "SELECT COALESCE(SUM(value), 0) as total FROM payments "
                "WHERE status IN ('RECEIVED', 'CONFIRMED') AND confirmed_at >= ?",
                (month_start,),
            ).fetchone()

    return {
        'plan_counts': plan_counts,
        'recent_payments': recent_payments,
        'total_revenue': revenue['total'] if revenue else 0,
        'monthly_revenue': monthly_revenue['total'] if monthly_revenue else 0,
    }
