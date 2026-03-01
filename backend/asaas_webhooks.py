"""Webhook handler para eventos do Asaas (pagamentos Pix)."""

from flask import Blueprint, request, jsonify

from backend.config import log
from backend.billing import (
    validate_webhook_token,
    is_webhook_processed, mark_webhook_processed,
    process_payment_received, process_payment_refunded,
)

asaas_bp = Blueprint('asaas_webhooks', __name__)


@asaas_bp.route('/api/webhooks/asaas', methods=['POST'])
def handle_asaas_webhook():
    """Recebe e processa webhooks do Asaas."""
    # Validar token
    token = request.headers.get('asaas-access-token', '')
    if not validate_webhook_token(token):
        log.warning('[WEBHOOK] Token invalido de %s', request.remote_addr)
        return jsonify({'error': 'Token invalido'}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Body invalido'}), 400

    event_type = data.get('event', '')
    payment_data = data.get('payment', {})
    payment_id = payment_data.get('id', '')

    # Gerar event_id unico para idempotencia
    event_id = '%s_%s' % (event_type, payment_id)

    # Idempotencia
    if is_webhook_processed(event_id):
        log.debug('[WEBHOOK] Evento ja processado: %s', event_id)
        return jsonify({'status': 'already_processed'}), 200

    log.info('[WEBHOOK] Evento recebido: %s payment=%s', event_type, payment_id)

    # Processar evento
    if event_type in ('PAYMENT_RECEIVED', 'PAYMENT_CONFIRMED'):
        process_payment_received(payment_data)
    elif event_type == 'PAYMENT_REFUNDED':
        process_payment_refunded(payment_data)
    else:
        log.info('[WEBHOOK] Evento ignorado: %s', event_type)

    # Marcar como processado
    mark_webhook_processed(event_id, event_type, payment_id)

    return jsonify({'status': 'ok'}), 200
