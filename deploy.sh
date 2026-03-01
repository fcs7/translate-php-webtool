#!/bin/bash
# =============================================================================
#  Deploy automatico — Trans-Script Web
#  Compativel: Debian 12 / Ubuntu 22+ e derivados
#
#  Idempotente: detecta o que ja esta instalado e pula etapas ja concluidas.
#
#  Uso:
#    chmod +x deploy.sh && sudo ./deploy.sh
# =============================================================================

set -euo pipefail

# --- Variaveis ---
APP_NAME="trans-script-web"
INSTALL_DIR="/opt/$APP_NAME"
VENV_DIR="$INSTALL_DIR/venv"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEPLOY_USER="${SUDO_USER:-$USER}"
ENV_FILE="/etc/trans-script-web/env"

# --- Cores ---
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${BLUE}[$(date +%H:%M:%S)]${NC} $1"; }
ok()   { echo -e "${GREEN}  ✓${NC} $1"; }
skip() { echo -e "${CYAN}  ↷${NC} $1 ${CYAN}(ja instalado)${NC}"; }
warn() { echo -e "${YELLOW}  !${NC} $1"; }
fail() { echo -e "${RED}  ✗${NC} $1"; exit 1; }

# --- Verificar root ---
if [ "$EUID" -ne 0 ]; then
    fail "Execute com sudo: sudo ./deploy.sh"
fi

echo ""
echo "=================================================="
echo "  Traducao — Deploy Automatico"
echo "  Origem:  $SCRIPT_DIR"
echo "  Destino: $INSTALL_DIR"
echo "=================================================="
echo ""

# =============================================================================
# Diagnostico inicial
# =============================================================================
echo -e "${BLUE}Diagnostico do sistema:${NC}"

_diag_python=$(python3 --version 2>/dev/null || echo "ausente")
_diag_node=$(node --version 2>/dev/null || echo "ausente")
_diag_nginx=$(systemctl is-active nginx 2>/dev/null || echo "inativo")
_diag_service=$(systemctl is-active "$APP_NAME" 2>/dev/null || echo "inativo")
_diag_venv=$([ -f "$VENV_DIR/bin/gunicorn" ] && echo "ok" || echo "ausente")
_diag_frontend=$([ -f "$INSTALL_DIR/backend/static/index.html" ] && echo "ok" || echo "ausente")
_diag_ssl=$([ -d "/etc/letsencrypt/live" ] && ls /etc/letsencrypt/live 2>/dev/null | head -1 || echo "nenhum")
_diag_env=$([ -f "$ENV_FILE" ] && echo "ok" || echo "ausente")

echo "  Python3    : $_diag_python"
echo "  Node.js    : $_diag_node"
echo "  Nginx      : $_diag_nginx"
echo "  Servico    : $_diag_service"
echo "  Virtualenv : $_diag_venv"
echo "  Frontend   : $_diag_frontend"
echo "  SSL certs  : $_diag_ssl"
echo "  Env file   : $_diag_env"
echo ""

# =============================================================================
# 0. Configuracao interativa (dominio + SMTP)
# =============================================================================

DOMAIN_VAL=""
SKIP_SMTP=0
SMTP_HOST_VAL="smtp.gmail.com"
SMTP_PORT_VAL="587"
SMTP_USER_VAL=""
SMTP_PASS_VAL=""
SMTP_FROM_VAL=""

# ── Dominio ──────────────────────────────────────────────────────────────────
# Tenta reutilizar dominio ja existente no certbot
_existing_domain=$(ls /etc/letsencrypt/live 2>/dev/null | grep -v README | head -1 || true)

echo -e "${BLUE}Dominio (SSL)${NC}"
if [ -n "$_existing_domain" ]; then
    echo "  Certificado existente detectado: $_existing_domain"
    read -r -p "  Usar este dominio? [S/n] " _resp
    if [[ ! "$_resp" =~ ^[Nn]$ ]]; then
        DOMAIN_VAL="$_existing_domain"
    fi
fi

if [ -z "$DOMAIN_VAL" ]; then
    echo "  Informe o dominio para ativar HTTPS via Let's Encrypt."
    echo "  O DNS deve apontar para este servidor. Enter para pular (HTTP apenas)."
    echo ""
    read -r -p "  Dominio (ex: app.felipecs.com) ou Enter para pular: " DOMAIN_VAL
    DOMAIN_VAL="${DOMAIN_VAL// /}"
fi
echo ""

if [ -n "$DOMAIN_VAL" ]; then
    ok "SSL sera configurado para: $DOMAIN_VAL"
else
    warn "Sem dominio — deploy apenas em HTTP"
fi
echo ""

# ── SMTP ─────────────────────────────────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
    echo -e "${YELLOW}Configuracao existente:${NC} $ENV_FILE"
    read -r -p "  Reconfigurar SMTP? [s/N] " _resp
    echo ""
    if [[ ! "$_resp" =~ ^[Ss]$ ]]; then
        SKIP_SMTP=1
        ok "Configuracao SMTP mantida"
        echo ""
    fi
fi

# ── DeepL API (opcional) ────────────────────────────────────────────────────
SKIP_DEEPL=0
DEEPL_KEY_VAL=""
if grep -q "^DEEPL_API_KEY=" "$ENV_FILE" 2>/dev/null; then
    DEEPL_KEY_VAL=$(grep "^DEEPL_API_KEY=" "$ENV_FILE" | cut -d= -f2 || true)
    SKIP_DEEPL=1
    skip "DeepL API Key ja configurada"
else
    echo -e "${BLUE}Traducao (opcional)${NC}"
    echo "  DeepL Free API Key — cadastro gratis em: https://www.deepl.com/pro#developer"
    read -r -p "  DeepL API Key (Enter para pular): " DEEPL_KEY_VAL
    DEEPL_KEY_VAL="${DEEPL_KEY_VAL// /}"
    if [ -n "$DEEPL_KEY_VAL" ]; then
        ok "DeepL API Key configurada"
    else
        warn "Sem DeepL — traducao usara apenas Google Free + MyMemory"
    fi
fi
echo ""

# ── Asaas API (pagamentos Pix) ────────────────────────────────────────────
SKIP_ASAAS=0
ASAAS_KEY_VAL=""
ASAAS_URL_VAL="https://api.asaas.com/api"
ASAAS_WEBHOOK_VAL=""
APP_BASE_URL_VAL=""
if grep -q "^ASAAS_API_KEY=" "$ENV_FILE" 2>/dev/null; then
    ASAAS_KEY_VAL=$(grep "^ASAAS_API_KEY=" "$ENV_FILE" | cut -d= -f2 || true)
    ASAAS_URL_VAL=$(grep "^ASAAS_API_URL=" "$ENV_FILE" | cut -d= -f2 || echo "https://api.asaas.com/api")
    ASAAS_WEBHOOK_VAL=$(grep "^ASAAS_WEBHOOK_TOKEN=" "$ENV_FILE" | cut -d= -f2 || true)
    APP_BASE_URL_VAL=$(grep "^APP_BASE_URL=" "$ENV_FILE" | cut -d= -f2 || true)
    SKIP_ASAAS=1
    skip "Asaas API ja configurada"
else
    echo -e "${BLUE}Pagamentos Pix — Asaas (opcional)${NC}"
    echo "  Cadastro em: https://www.asaas.com/"
    read -r -p "  Asaas API Key (Enter para pular): " ASAAS_KEY_VAL
    ASAAS_KEY_VAL="${ASAAS_KEY_VAL// /}"
    if [ -n "$ASAAS_KEY_VAL" ]; then
        read -r -p "  URL da API [https://api.asaas.com/api]: " _asaas_url
        ASAAS_URL_VAL="${_asaas_url:-$ASAAS_URL_VAL}"
        ASAAS_WEBHOOK_VAL=$(python3 -c "import secrets; print(secrets.token_hex(32))")
        if [ -n "$DOMAIN_VAL" ]; then
            APP_BASE_URL_VAL="https://$DOMAIN_VAL"
        else
            APP_BASE_URL_VAL="http://$IP"
        fi
        ok "Asaas configurado — Webhook token gerado automaticamente"
        echo "  Webhook URL: $APP_BASE_URL_VAL/api/webhooks/asaas"
        echo "  Webhook Token: $ASAAS_WEBHOOK_VAL"
        echo "  Configure este token no painel Asaas > Integracoes > Webhooks"
    else
        warn "Sem Asaas — pagamentos Pix nao estarao disponiveis"
    fi
fi
echo ""

if [ "$SKIP_SMTP" -eq 0 ]; then
    echo -e "${BLUE}Configuracao de e-mail (OTP)${NC}"
    echo "  Para Gmail: use uma App Password (myaccount.google.com/apppasswords)"
    echo ""

    while true; do
        read -r -p "  E-mail remetente: " SMTP_USER_VAL
        SMTP_USER_VAL="${SMTP_USER_VAL// /}"
        [[ "$SMTP_USER_VAL" =~ ^[^@]+@[^@]+\.[^@]+$ ]] && break
        warn "E-mail invalido. Tente novamente."
    done

    while true; do
        read -r -s -p "  Senha / App Password (nao aparece): " SMTP_PASS_VAL
        echo ""
        [ -n "$SMTP_PASS_VAL" ] && break
        warn "Senha nao pode ser vazia."
    done

    read -r -p "  Nome do remetente [Traducao]: " _from_name
    _from_name="${_from_name:-Traducao}"
    SMTP_FROM_VAL="$_from_name <$SMTP_USER_VAL>"

    read -r -p "  Servidor SMTP [$SMTP_HOST_VAL]: " _host
    SMTP_HOST_VAL="${_host:-$SMTP_HOST_VAL}"

    read -r -p "  Porta SMTP [$SMTP_PORT_VAL]: " _port
    SMTP_PORT_VAL="${_port:-$SMTP_PORT_VAL}"

    echo ""
    echo "  Resumo:"
    echo "    Servidor : $SMTP_HOST_VAL:$SMTP_PORT_VAL"
    echo "    Conta    : $SMTP_USER_VAL"
    echo "    Remetente: $SMTP_FROM_VAL"
    echo ""
    read -r -p "  Confirmar? [S/n] " _conf
    [[ "$_conf" =~ ^[Nn]$ ]] && fail "Cancelado pelo usuario."
    echo ""
fi

# =============================================================================
# 1. Dependencias do sistema
# =============================================================================
log "[1/6] Dependencias do sistema..."

apt-get update -qq
apt-get install -f -y -qq 2>/dev/null || true
dpkg --configure -a 2>/dev/null || true

# Pacotes base (idempotente — apt pula se ja instalado)
apt-get install -y \
    python3 python3-venv python3-pip \
    nginx rsync curl \
    certbot python3-certbot-nginx \
    2>&1 | grep -E "^(Err|W:|E:|Setting up|Installing)" | sed 's/^/  /' || true

ok "Pacotes base verificados"

# Node.js
NODE_OK=0
if command -v node &>/dev/null; then
    _nver=$(node -e "process.exit(parseInt(process.versions.node)<18?1:0)" 2>/dev/null; echo $?)
    [ "$_nver" = "0" ] && NODE_OK=1
fi
if [ "$NODE_OK" -eq 1 ]; then
    skip "Node.js $(node -v)"
else
    if apt-get install -y nodejs npm 2>/dev/null; then
        NODE_OK=1
    else
        warn "Instalando Node.js 20 via NodeSource..."
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - > /dev/null 2>&1
        apt-get install -y nodejs || fail "Falha ao instalar Node.js"
    fi
    ok "Node.js $(node -v) instalado"
fi

# translate-shell
if command -v trans &>/dev/null; then
    skip "translate-shell"
else
    apt-get install -y -qq translate-shell 2>/dev/null \
        || (wget -q -O /usr/local/bin/trans \
                "https://raw.githubusercontent.com/soimort/translate-shell/gh-pages/trans" \
            && chmod +x /usr/local/bin/trans \
            && ok "translate-shell instalado via wget")
fi

# unrar (opcional)
if command -v unrar &>/dev/null; then
    skip "unrar"
else
    apt-get install -y -qq unrar 2>/dev/null \
        || apt-get install -y -qq unrar-free 2>/dev/null \
        || warn "unrar nao disponivel — arquivos .rar nao serao suportados"
fi

# =============================================================================
# 2. Copiar projeto para /opt
# =============================================================================
log "[2/6] Sincronizando projeto em $INSTALL_DIR..."

mkdir -p "$INSTALL_DIR"
rsync -a --exclude='venv' --exclude='node_modules' --exclude='.git' \
    --exclude='backend/uploads' --exclude='backend/jobs' --exclude='backend/static' \
    "$SCRIPT_DIR/" "$INSTALL_DIR/"

mkdir -p "$INSTALL_DIR/backend/uploads" \
         "$INSTALL_DIR/backend/jobs" \
         "$INSTALL_DIR/backend/static"
chown -R "$DEPLOY_USER":"$DEPLOY_USER" "$INSTALL_DIR"

ok "Projeto sincronizado"

# =============================================================================
# 3. Python virtualenv
# =============================================================================
log "[3/6] Ambiente Python..."

if [ -f "$VENV_DIR/bin/gunicorn" ]; then
    # Virtualenv ja existe — apenas atualiza dependencias
    skip "virtualenv existente — atualizando dependencias"
    sudo -u "$DEPLOY_USER" "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    sudo -u "$DEPLOY_USER" "$VENV_DIR/bin/pip" install --quiet \
        -r "$INSTALL_DIR/backend/requirements.txt"
    ok "Dependencias Python atualizadas"
else
    sudo -u "$DEPLOY_USER" python3 -m venv "$VENV_DIR"
    sudo -u "$DEPLOY_USER" "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    sudo -u "$DEPLOY_USER" "$VENV_DIR/bin/pip" install --quiet \
        -r "$INSTALL_DIR/backend/requirements.txt"
    ok "Virtualenv criado em $VENV_DIR"
fi

# =============================================================================
# 4. Frontend build
# =============================================================================
log "[4/6] Frontend React..."

cd "$INSTALL_DIR/frontend"
sudo -u "$DEPLOY_USER" npm install --silent 2>&1 | tail -5 | sed 's/^/  /'
if sudo -u "$DEPLOY_USER" npm run build 2>&1 | tail -10 | sed 's/^/  /'; then
    ok "Frontend compilado"
else
    fail "Erro ao compilar frontend — veja acima"
fi
cd "$INSTALL_DIR"

# =============================================================================
# 5. Nginx + SSL
# =============================================================================
log "[5/6] Nginx..."

NGINX_CONF="/etc/nginx/sites-available/$APP_NAME"
cp "$INSTALL_DIR/config/nginx.conf" "$NGINX_CONF"

if [ -n "$DOMAIN_VAL" ]; then
    sed -i "s/server_name _;/server_name $DOMAIN_VAL;/" "$NGINX_CONF"
fi

ln -sf "$NGINX_CONF" "/etc/nginx/sites-enabled/$APP_NAME"
[ -f /etc/nginx/sites-enabled/default ] && rm -f /etc/nginx/sites-enabled/default

nginx -t > /dev/null 2>&1 || fail "Configuracao Nginx invalida"
systemctl reload nginx
ok "Nginx configurado"

# ── SSL via Certbot (totalmente non-interactive) ─────────────────────────────
if [ -n "$DOMAIN_VAL" ]; then
    CERT_PATH="/etc/letsencrypt/live/$DOMAIN_VAL/fullchain.pem"

    # Obter email para certbot (usado em ambos os casos)
    if [ "$SKIP_SMTP" -eq 0 ]; then
        _certbot_email="$SMTP_USER_VAL"
    else
        _certbot_email=$(grep "^SMTP_USER" "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo "admin@$DOMAIN_VAL")
    fi

    if [ -f "$CERT_PATH" ]; then
        # Certificado ja existe — reaplicar SSL ao Nginx (config foi sobrescrita acima)
        skip "certificado SSL existente — reaplicando ao Nginx"
        # Tenta 'install' com --cert-name; se falhar, tenta 'certbot --nginx' completo
        if certbot install --nginx \
            -d "$DOMAIN_VAL" \
            --cert-name "$DOMAIN_VAL" \
            --non-interactive \
            --redirect \
            2>&1 | sed 's/^/    /'; then
            :
        elif certbot --nginx \
            -d "$DOMAIN_VAL" \
            --non-interactive \
            --agree-tos \
            --no-eff-email \
            -m "$_certbot_email" \
            --redirect \
            --keep-existing \
            2>&1 | sed 's/^/    /'; then
            :
        else
            warn "certbot falhou — tente: sudo certbot --nginx -d $DOMAIN_VAL --non-interactive --agree-tos --redirect -m $_certbot_email"
        fi
        certbot renew --quiet --nginx 2>/dev/null || true
        ok "SSL reaplicado ao Nginx"
    else
        log "  Obtendo certificado SSL para $DOMAIN_VAL..."

        if certbot --nginx \
            -d "$DOMAIN_VAL" \
            --non-interactive \
            --agree-tos \
            --no-eff-email \
            -m "$_certbot_email" \
            --redirect \
            2>&1 | sed 's/^/    /'; then
            ok "SSL ativado — https://$DOMAIN_VAL"
        else
            warn "Certbot falhou — verifique se DNS $DOMAIN_VAL aponta para este IP"
            warn "Para tentar depois: sudo certbot --nginx -d $DOMAIN_VAL --non-interactive --agree-tos --redirect -m $_certbot_email"
        fi
    fi
fi

# =============================================================================
# 6. Arquivo de env + systemd
# =============================================================================
log "[6/6] Servico systemd..."

mkdir -p "/etc/trans-script-web"
chmod 700 "/etc/trans-script-web"

if [ "$SKIP_SMTP" -eq 0 ]; then
    SECRET_KEY_VAL=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    install -m 600 /dev/null "$ENV_FILE"
    cat > "$ENV_FILE" << EOF
# Trans-Script Web — variaveis de ambiente
# Gerado em: $(date)
# Para alterar: nano $ENV_FILE && systemctl restart $APP_NAME

SECRET_KEY=$SECRET_KEY_VAL

SMTP_HOST=$SMTP_HOST_VAL
SMTP_PORT=$SMTP_PORT_VAL
SMTP_USER=$SMTP_USER_VAL
SMTP_PASS=$SMTP_PASS_VAL
SMTP_FROM=$SMTP_FROM_VAL

DEEPL_API_KEY=$DEEPL_KEY_VAL

# Asaas (pagamentos Pix)
ASAAS_API_KEY=$ASAAS_KEY_VAL
ASAAS_API_URL=$ASAAS_URL_VAL
ASAAS_WEBHOOK_TOKEN=$ASAAS_WEBHOOK_VAL
APP_BASE_URL=$APP_BASE_URL_VAL
EOF
    chmod 600 "$ENV_FILE"
    chown root:root "$ENV_FILE"
    ok "Configuracao salva em $ENV_FILE"
else
    skip "env file existente"
    # Adicionar vars Asaas ao env existente (se novas)
    if [ "$SKIP_ASAAS" -eq 0 ] && [ -n "$ASAAS_KEY_VAL" ]; then
        {
            echo ""
            echo "# Asaas (pagamentos Pix) — adicionado em $(date)"
            echo "ASAAS_API_KEY=$ASAAS_KEY_VAL"
            echo "ASAAS_API_URL=$ASAAS_URL_VAL"
            echo "ASAAS_WEBHOOK_TOKEN=$ASAAS_WEBHOOK_VAL"
            echo "APP_BASE_URL=$APP_BASE_URL_VAL"
        } >> "$ENV_FILE"
        ok "Variaveis Asaas adicionadas ao env existente"
    fi
fi

cat > "/etc/systemd/system/$APP_NAME.service" << UNIT
[Unit]
Description=Traducao — Tradutor PHP EN→PT-BR
After=network.target

[Service]
Type=simple
User=$DEPLOY_USER
WorkingDirectory=$INSTALL_DIR
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/gunicorn \\
    --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker \\
    --workers 1 \\
    --bind 127.0.0.1:5000 \\
    --timeout 300 \\
    backend.wsgi:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "$APP_NAME" > /dev/null 2>&1
systemctl restart "$APP_NAME"

sleep 2

if systemctl is-active --quiet "$APP_NAME"; then
    ok "Servico iniciado e habilitado no boot"
else
    echo ""
    echo "  Ultimas linhas do log:"
    journalctl -u "$APP_NAME" -n 20 --no-pager | sed 's/^/    /'
    fail "Servico nao iniciou — veja: journalctl -u $APP_NAME -f"
fi

# =============================================================================
# Resultado
# =============================================================================
IP=$(hostname -I | awk '{print $1}')
echo ""
echo "=================================================="
echo -e "  ${GREEN}Deploy concluido!${NC}"
echo ""
if [ -n "$DOMAIN_VAL" ] && [ -f "/etc/letsencrypt/live/$DOMAIN_VAL/fullchain.pem" ]; then
    echo "  URL:       https://$DOMAIN_VAL"
elif [ -n "$DOMAIN_VAL" ]; then
    echo "  URL:       http://$DOMAIN_VAL  (SSL pendente)"
else
    echo "  URL:       http://$IP"
fi
echo "  Env:       $ENV_FILE"
echo "  Status:    systemctl status $APP_NAME"
echo "  Logs:      journalctl -u $APP_NAME -f"
echo "  Reiniciar: systemctl restart $APP_NAME"
echo "=================================================="
echo ""
