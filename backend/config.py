"""Configuracoes centralizadas do backend."""

import os
import logging

# Diretorios base
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)

# Armazenamento
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
JOBS_FOLDER = os.path.join(BASE_DIR, 'jobs')
STATIC_FOLDER = os.path.join(BASE_DIR, 'static')
LOG_FILE = os.path.join(BASE_DIR, 'trans-script.log')

# Limites
MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100 MB
MAX_CONCURRENT_JOBS = 3
RATE_LIMIT_SECONDS = 5  # Intervalo minimo entre uploads por IP

# Traducao
DEFAULT_DELAY = 0.2
SOURCE_LANG = 'en'
TARGET_LANG = 'pt-br'

# Providers de traducao
DEEPL_API_KEY = os.environ.get('DEEPL_API_KEY', '')
MYMEMORY_EMAIL = os.environ.get('MYMEMORY_EMAIL', '')
CACHE_MEMORY_SIZE = int(os.environ.get('CACHE_MEMORY_SIZE', '10000'))

# Autenticacao
SECRET_KEY = os.environ.get('SECRET_KEY', os.urandom(32).hex())
DB_PATH = os.path.join(BASE_DIR, 'users.db')
OTP_EXPIRY_MINUTES = 15
OTP_MAX_ATTEMPTS = 3

# Sessao admin (criptografia forte)
ADMIN_SESSION_EXPIRY_HOURS = int(os.environ.get('ADMIN_SESSION_EXPIRY_HOURS', '4'))
ADMIN_EMAILS = [
    e.strip().lower()
    for e in os.environ.get('ADMIN_EMAILS', '').split(',')
    if e.strip()
]

# SMTP (via variaveis de ambiente)
SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER = os.environ.get('SMTP_USER', '')
SMTP_PASS = os.environ.get('SMTP_PASS', '')
SMTP_FROM = os.environ.get('SMTP_FROM', 'Traducao <noreply@example.com>')

# Asaas — pagamentos Pix
ASAAS_API_KEY = os.environ.get('ASAAS_API_KEY', '')
ASAAS_API_URL = os.environ.get('ASAAS_API_URL', 'https://api.asaas.com/api')
ASAAS_WEBHOOK_TOKEN = os.environ.get('ASAAS_WEBHOOK_TOKEN', '')
APP_BASE_URL = os.environ.get('APP_BASE_URL', 'http://localhost:5000')

# Planos de assinatura (pagamento avulso 30 dias)
PLAN_STORAGE_LIMITS = {
    'free': 524_288_000,       # 500 MB
    'pro': 2_147_483_648,      # 2 GB
    'business': 10_737_418_240, # 10 GB
}
PLAN_PRICES = {
    'pro': 29.00,
    'business': 79.00,
}
PLAN_DURATION_DAYS = 30

# Garantir que diretorios existem
for _folder in [UPLOAD_FOLDER, JOBS_FOLDER]:
    os.makedirs(_folder, exist_ok=True)


# ============================================================================
# Logging
# ============================================================================

def setup_logging():
    """Configura logging para console + arquivo."""
    fmt = logging.Formatter(
        '[%(asctime)s] %(levelname)s %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    # Arquivo
    fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    # Console
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    root = logging.getLogger('trans-script')
    root.setLevel(logging.DEBUG)
    root.addHandler(fh)
    root.addHandler(ch)

    return root


log = setup_logging()
