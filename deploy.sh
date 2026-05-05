#!/usr/bin/env bash
# =============================================================================
# AI Shorts System — One-Command Deploy
# Usage: ./deploy.sh <ssh_target>
# Example: ./deploy.sh nodejs   (SSH alias)
#          ./deploy.sh aditya@1.2.3.4
# =============================================================================
set -euo pipefail

# ─── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'
BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

# ─── Config ──────────────────────────────────────────────────────────────────
PROJECT_DIR="/opt/ai-shorts-system"
DOCKER_PROJECT="ai_shorts_system"
DOMAIN="video.theadityashah.com"
DEFAULT_UI_PORT=8787
DEFAULT_API_PORT=8788
DEFAULT_N8N_PORT=8789

# ─── Args ────────────────────────────────────────────────────────────────────
SSH_TARGET="${1:-}"
if [[ -z "$SSH_TARGET" ]]; then
    echo -e "${BOLD}Usage:${NC} ./deploy.sh <ssh_target>"
    echo "  Examples:"
    echo "    ./deploy.sh nodejs"
    echo "    ./deploy.sh aditya@your-server-ip"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ─── Helper: Remote exec ────────────────────────────────────────────────────
rcmd() {
    ssh -o ConnectTimeout=10 "$SSH_TARGET" "$@"
}

# =============================================================================
# STEP 1: SSH PREFLIGHT
# =============================================================================
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  AI Shorts System — Deployment${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
echo ""

info "Testing SSH connection to ${SSH_TARGET}..."
ssh -o BatchMode=yes -o ConnectTimeout=5 "$SSH_TARGET" "echo SSH_OK" >/dev/null 2>&1 \
    || fail "SSH connection failed. Check your SSH config for '${SSH_TARGET}'."
ok "SSH connection successful"

info "Running server inspection..."
SERVER_USER=$(rcmd "whoami")
SERVER_OS=$(rcmd "uname -a" 2>/dev/null | head -1)
DOCKER_STATUS=$(rcmd "docker --version 2>/dev/null" || echo "NOT INSTALLED")
COMPOSE_STATUS=$(rcmd "docker compose version 2>/dev/null" || echo "NOT INSTALLED")

echo ""
echo -e "${BOLD}=== PREFLIGHT REPORT ===${NC}"
echo -e "  SSH Target:      ${SSH_TARGET}"
echo -e "  User:            ${SERVER_USER}"
echo -e "  OS:              ${SERVER_OS}"
echo -e "  Docker:          ${DOCKER_STATUS}"
echo -e "  Docker Compose:  ${COMPOSE_STATUS}"
echo -e "${BOLD}========================${NC}"
echo ""

# Check Docker
if [[ "$DOCKER_STATUS" == "NOT INSTALLED" ]]; then
    fail "Docker is not installed on the server. Install Docker first."
fi
if [[ "$COMPOSE_STATUS" == "NOT INSTALLED" ]]; then
    fail "Docker Compose is not installed on the server."
fi

# Check write access
info "Checking write access to ${PROJECT_DIR}..."
rcmd "mkdir -p ${PROJECT_DIR}" 2>/dev/null \
    || rcmd "sudo mkdir -p ${PROJECT_DIR} && sudo chown \$(whoami):\$(whoami) ${PROJECT_DIR}" \
    || fail "Cannot create ${PROJECT_DIR}. Check permissions."
ok "Write access confirmed"

# ─── Port detection ──────────────────────────────────────────────────────────
# If the server already has a .env with ports, reuse them (re-deploy case).
# Only do free-port detection on a fresh install.
info "Detecting available ports..."
REMOTE_ENV_EXISTS=$(rcmd "test -f ${PROJECT_DIR}/.env && echo yes || echo no")
if [[ "$REMOTE_ENV_EXISTS" == "yes" ]]; then
    REMOTE_UI_PORT=$(rcmd "grep '^UI_PORT=' ${PROJECT_DIR}/.env 2>/dev/null | cut -d= -f2-" || true)
    REMOTE_API_PORT=$(rcmd "grep '^API_PORT=' ${PROJECT_DIR}/.env 2>/dev/null | cut -d= -f2-" || true)
    REMOTE_N8N_PORT=$(rcmd "grep '^N8N_PORT=' ${PROJECT_DIR}/.env 2>/dev/null | cut -d= -f2-" || true)
    UI_PORT="${REMOTE_UI_PORT:-$DEFAULT_UI_PORT}"
    API_PORT="${REMOTE_API_PORT:-$DEFAULT_API_PORT}"
    N8N_PORT="${REMOTE_N8N_PORT:-$DEFAULT_N8N_PORT}"
    ok "Reusing existing ports: UI=${UI_PORT}, API=${API_PORT}, n8n=${N8N_PORT}"
else
    find_free_port() {
        local port=$1
        while rcmd "ss -tuln | grep -q ':${port} '" 2>/dev/null; do
            warn "Port ${port} is occupied, trying $((port + 1))..." >&2
            port=$((port + 1))
        done
        echo "$port"
    }
    UI_PORT=$(find_free_port $DEFAULT_UI_PORT)
    API_PORT=$(find_free_port $DEFAULT_API_PORT)
    N8N_PORT=$(find_free_port $DEFAULT_N8N_PORT)
    ok "Detected free ports: UI=${UI_PORT}, API=${API_PORT}, n8n=${N8N_PORT}"
fi

# =============================================================================
# STEP 2: UPLOAD PROJECT FILES
# =============================================================================
info "Uploading project files..."

# Create a tarball of the project (excluding .env with secrets, .git, __pycache__)
cd "$SCRIPT_DIR"
tar czf /tmp/ai-shorts-deploy.tar.gz \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.env' \
    --exclude='data' \
    --exclude='*.db' \
    .

scp -o ConnectTimeout=10 /tmp/ai-shorts-deploy.tar.gz "${SSH_TARGET}:${PROJECT_DIR}/deploy.tar.gz"
rcmd "cd ${PROJECT_DIR} && tar xzf deploy.tar.gz && rm deploy.tar.gz"
rm -f /tmp/ai-shorts-deploy.tar.gz
ok "Project files uploaded"

# =============================================================================
# STEP 3: SETUP .env (DO NOT OVERWRITE EXISTING)
# =============================================================================
info "Setting up environment..."
rcmd "cd ${PROJECT_DIR} && if [ ! -f .env ]; then cp .env.example .env; echo 'Created .env from template'; else echo '.env already exists, preserving'; fi"

# Update ports in .env
rcmd "cd ${PROJECT_DIR} && \
    sed -i 's/^UI_PORT=.*/UI_PORT=${UI_PORT}/' .env && \
    sed -i 's/^API_PORT=.*/API_PORT=${API_PORT}/' .env && \
    sed -i 's/^N8N_PORT=.*/N8N_PORT=${N8N_PORT}/' .env"

# Always sync all credential vars from local .env to remote (safe, handles special chars)
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    info "Syncing credentials from local .env to server..."
    SYNC_TMP=$(mktemp)
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" =~ ^#.*$ || -z "${line// }" ]] && continue
        key="${line%%=*}"
        if [[ "$key" =~ ^(AZURE_OPENAI_|AZURE_SPEECH_|PEXELS_|DID_|ELEVENLABS_|AI_PROVIDER|VOICE_PROVIDER|OPENAI_API_KEY|MAI_VOICE_|GOOGLE_CLIENT_ID|GOOGLE_CLIENT_SECRET|ALLOWED_EMAIL|BASE_URL|SECRET_KEY|VOICEBOX_|KOKORO_) ]]; then
            echo "$line" >> "$SYNC_TMP"
        fi
    done < "$SCRIPT_DIR/.env"
    scp -o ConnectTimeout=10 "$SYNC_TMP" "${SSH_TARGET}:/tmp/.env.sync"
    rm -f "$SYNC_TMP"

    # Merge sync file into remote .env using Python (safe for all special chars)
    rcmd "python3 /dev/stdin << 'PYEOF'
import os
sync_vars = {}
with open('/tmp/.env.sync') as f:
    for line in f:
        line = line.rstrip('\n')
        if '=' in line and not line.startswith('#'):
            k, _, v = line.partition('=')
            sync_vars[k.strip()] = v
env_path = '${PROJECT_DIR}/.env'
lines = []
updated = set()
with open(env_path) as f:
    for line in f:
        stripped = line.rstrip('\n')
        if '=' in stripped and not stripped.startswith('#'):
            k = stripped.split('=', 1)[0].strip()
            if k in sync_vars:
                lines.append(k + '=' + sync_vars[k] + '\n')
                updated.add(k)
                continue
        lines.append(line if line.endswith('\n') else line + '\n')
for k, v in sync_vars.items():
    if k not in updated:
        lines.append(k + '=' + v + '\n')
with open(env_path, 'w') as f:
    f.writelines(lines)
os.remove('/tmp/.env.sync')
print('Synced keys:', list(sync_vars.keys()))
PYEOF
"
    ok "Credentials synced from local .env"
fi

ok "Environment configured"

# =============================================================================
# STEP 4: BUILD AND START CONTAINERS
# =============================================================================
info "Building and starting Docker containers..."
rcmd "cd ${PROJECT_DIR} && docker compose -p ${DOCKER_PROJECT} build --quiet 2>&1 | tail -5"
# Activate the voicebox profile if VOICE_PROVIDER=voicebox is set in .env
_local_vp=$(grep '^VOICE_PROVIDER=' "${BASH_SOURCE[0]%/*}/.env" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || echo "")
_compose_profile_flags=""
[[ "$_local_vp" == "voicebox" ]] && _compose_profile_flags="--profile voicebox"
rcmd "cd ${PROJECT_DIR} && docker compose -p ${DOCKER_PROJECT} up -d ${_compose_profile_flags} 2>&1 | tail -10"
ok "Containers started"

# Wait for health
info "Waiting for services to be ready..."
for i in {1..30}; do
    if rcmd "curl -sf http://127.0.0.1:${UI_PORT}/health >/dev/null 2>&1"; then
        ok "Backend is healthy"
        break
    fi
    if [[ $i -eq 30 ]]; then
        warn "Backend health check timed out. It may still be starting."
    fi
    sleep 2
done

###############################################################################
# STEP 4.5: SEED KNOWLEDGE BASE FROM SEED_KNOWLEDGE.PY
###############################################################################
info "Seeding knowledge base from seed_knowledge.py..."
BACKEND_CONTAINER=$(rcmd "cd ${PROJECT_DIR} && docker compose -p ${DOCKER_PROJECT} ps -q backend | head -1")
if [[ -n "$BACKEND_CONTAINER" ]]; then
    rcmd "cd ${PROJECT_DIR} && docker cp backend/seed_knowledge.py $BACKEND_CONTAINER:/app/seed_knowledge.py"
    rcmd "cd ${PROJECT_DIR} && docker exec $BACKEND_CONTAINER python /app/seed_knowledge.py || true"
    ok "Knowledge base seeded"
else
    warn "Backend container not found, skipping knowledge seeding."
fi

# =============================================================================
# STEP 5: NGINX CONFIGURATION
# =============================================================================
info "Configuring nginx reverse proxy..."

NGINX_EXISTS=$(rcmd "which nginx >/dev/null 2>&1 && echo yes || echo no")

if [[ "$NGINX_EXISTS" == "yes" ]]; then
    NGINX_CONF="ai-shorts-system"

    # Install WebSocket upgrade map (must be in http context → conf.d)
    rcmd "cat > /tmp/ws-map.conf << 'WSMAP_EOF'
map \$http_upgrade \$connection_upgrade {
    default  upgrade;
    ''       '';
}
WSMAP_EOF
sudo cp /tmp/ws-map.conf /etc/nginx/conf.d/ws-map.conf && rm /tmp/ws-map.conf"

    # Check if config already exists
    EXISTING_CONF=$(rcmd "test -f /etc/nginx/sites-available/${NGINX_CONF} && echo yes || echo no")

    # Generate nginx config
    rcmd "cat > /tmp/${NGINX_CONF} << 'NGINX_EOF'
# =============================================================================
# AI Shorts System — Nginx Reverse Proxy
# Auto-generated by deploy.sh — DO NOT edit other site configs
# =============================================================================

server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
        try_files \\\$uri =404;
    }

    location / {
        return 301 https://\\\$host\\\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${DOMAIN};

    # SSL certs (will be created by certbot)
    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;

    # Security headers
    add_header X-Frame-Options \"SAMEORIGIN\" always;
    add_header X-Content-Type-Options \"nosniff\" always;
    add_header X-XSS-Protection \"1; mode=block\" always;

    client_max_body_size 100M;

    # Main UI + API
    location / {
        proxy_pass http://127.0.0.1:${UI_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \\\$host;
        proxy_set_header X-Real-IP \\\$remote_addr;
        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \\\$scheme;
        proxy_read_timeout 120s;
    }

    # API (direct)
    location /api/ {
        proxy_pass http://127.0.0.1:${API_PORT}/;
        proxy_http_version 1.1;
        proxy_set_header Host \\\$host;
        proxy_set_header X-Real-IP \\\$remote_addr;
        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \\\$scheme;
        proxy_read_timeout 120s;
    }

    # n8n
    location /n8n/ {
        proxy_pass http://127.0.0.1:${N8N_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \\\$http_upgrade;
        proxy_set_header Connection \\\$connection_upgrade;
        proxy_set_header Host \\\$host;
        proxy_set_header X-Real-IP \\\$remote_addr;
        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \\\$scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 10s;
    }
}
NGINX_EOF"

    # Install config safely
    if [[ "$EXISTING_CONF" == "yes" ]]; then
        info "Nginx config exists, updating..."
    else
        info "Creating new nginx site config..."
    fi

    rcmd "sudo cp /tmp/${NGINX_CONF} /etc/nginx/sites-available/${NGINX_CONF}"
    rcmd "sudo ln -sf /etc/nginx/sites-available/${NGINX_CONF} /etc/nginx/sites-enabled/${NGINX_CONF}"
    rcmd "rm -f /tmp/${NGINX_CONF}"

    # Test nginx config
    if rcmd "sudo nginx -t 2>&1 | grep -q 'successful'"; then
        ok "Nginx config valid"
    else
        warn "Nginx config test failed - SSL cert may not exist yet. Will fix after certbot."
        # Create a temporary HTTP-only config for certbot
        rcmd "cat > /tmp/${NGINX_CONF}-http << 'NGINX_HTTP_EOF'
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
        try_files \\\$uri =404;
    }

    location / {
        proxy_pass http://127.0.0.1:${UI_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \\\$host;
        proxy_set_header X-Real-IP \\\$remote_addr;
        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
    }
}
NGINX_HTTP_EOF"
        rcmd "sudo cp /tmp/${NGINX_CONF}-http /etc/nginx/sites-available/${NGINX_CONF}"
        rcmd "rm -f /tmp/${NGINX_CONF}-http"
    fi

    rcmd "sudo systemctl reload nginx 2>/dev/null || sudo nginx -s reload 2>/dev/null" || warn "Could not reload nginx"
    ok "Nginx configured"

    # ─── DNS Check & SSL ─────────────────────────────────────────────────────
    info "Checking DNS for ${DOMAIN}..."
    SERVER_IP=$(rcmd "curl -sf https://api.ipify.org 2>/dev/null || curl -sf https://ifconfig.me 2>/dev/null || echo ''")
    DOMAIN_IP=$(dig +short "${DOMAIN}" 2>/dev/null | tail -1 || true)

    if [[ -n "$SERVER_IP" && "$DOMAIN_IP" == "$SERVER_IP" ]]; then
        ok "DNS resolves correctly: ${DOMAIN} → ${SERVER_IP}"

        # Install certbot if needed
        info "Setting up SSL certificate..."
        rcmd "which certbot >/dev/null 2>&1 || sudo apt-get update -qq && sudo apt-get install -y -qq certbot python3-certbot-nginx >/dev/null 2>&1" || true
        rcmd "sudo mkdir -p /var/www/certbot"

        # Check if cert already exists
        CERT_EXISTS=$(rcmd "test -d /etc/letsencrypt/live/${DOMAIN} && echo yes || echo no")

        if [[ "$CERT_EXISTS" == "yes" ]]; then
            ok "SSL certificate already exists"
        else
            info "Obtaining SSL certificate via certbot..."
            if rcmd "sudo certbot --nginx -d ${DOMAIN} --non-interactive --agree-tos -m admin@theadityashah.com 2>&1 | tail -5"; then
                ok "SSL certificate obtained"
            else
                warn "Certbot failed. You may need to run it manually."
            fi
        fi

        # Re-install the full HTTPS config now that cert exists
        rcmd "cat > /tmp/${NGINX_CONF} << 'NGINX_FULL_EOF'
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
        try_files \\\$uri =404;
    }

    location / {
        return 301 https://\\\$host\\\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${DOMAIN};

    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;

    add_header X-Frame-Options \"SAMEORIGIN\" always;
    add_header X-Content-Type-Options \"nosniff\" always;
    add_header X-XSS-Protection \"1; mode=block\" always;

    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:${UI_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \\\$host;
        proxy_set_header X-Real-IP \\\$remote_addr;
        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \\\$scheme;
        proxy_read_timeout 120s;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:${API_PORT}/;
        proxy_http_version 1.1;
        proxy_set_header Host \\\$host;
        proxy_set_header X-Real-IP \\\$remote_addr;
        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \\\$scheme;
        proxy_read_timeout 120s;
    }

    location /n8n/ {
        proxy_pass http://127.0.0.1:${N8N_PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \\\$http_upgrade;
        proxy_set_header Connection \\\$connection_upgrade;
        proxy_set_header Host \\\$host;
        proxy_set_header X-Real-IP \\\$remote_addr;
        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \\\$scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 10s;
    }
}
NGINX_FULL_EOF"
        rcmd "sudo cp /tmp/${NGINX_CONF} /etc/nginx/sites-available/${NGINX_CONF}"
        rcmd "rm -f /tmp/${NGINX_CONF}"
        rcmd "sudo nginx -t 2>/dev/null && sudo systemctl reload nginx 2>/dev/null" || true

        # Verify HTTPS
        info "Verifying HTTPS access..."
        sleep 2
        if curl -sf "https://${DOMAIN}/health" >/dev/null 2>&1; then
            ok "HTTPS is working! ✓"
        else
            warn "HTTPS verification pending. May take a moment to propagate."
        fi
    else
        warn "DNS mismatch: ${DOMAIN} → ${DOMAIN_IP:-unresolved} (server: ${SERVER_IP:-unknown})"
        warn "Skipping SSL. Configure DNS first, then re-run deploy."
    fi
else
    warn "Nginx not found on server."
    echo ""
    echo -e "${YELLOW}Manual nginx setup required:${NC}"
    echo "  1. Install nginx: sudo apt install nginx"
    echo "  2. Copy nginx config: see nginx/ai-shorts-system.conf"
    echo "  3. Run certbot: sudo certbot --nginx -d ${DOMAIN}"
fi

# =============================================================================
# STEP 6: VERIFY AND PRINT SUMMARY
# =============================================================================
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  ✓ DEPLOYMENT COMPLETE${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}UI:${NC}     http://${SSH_TARGET%%@*}:${UI_PORT}"
echo -e "  ${BOLD}API:${NC}    http://${SSH_TARGET%%@*}:${API_PORT}"
echo -e "  ${BOLD}n8n:${NC}    http://${SSH_TARGET%%@*}:${N8N_PORT}"
echo -e "  ${BOLD}Domain:${NC} https://${DOMAIN}"
echo -e "  ${BOLD}API Docs:${NC} https://${DOMAIN}/api/docs"
echo ""
echo -e "  ${BOLD}Project:${NC} ${PROJECT_DIR}"
echo -e "  ${BOLD}Logs:${NC}   ssh ${SSH_TARGET} 'cd ${PROJECT_DIR} && docker compose -p ${DOCKER_PROJECT} logs -f'"
echo ""
echo -e "  ${YELLOW}Note:${NC} Configure Azure OpenAI API keys in Settings page"
echo -e "  ${YELLOW}Note:${NC} or edit ${PROJECT_DIR}/.env on server"
echo ""
