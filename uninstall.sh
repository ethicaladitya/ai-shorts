#!/usr/bin/env bash
# =============================================================================
# AI Shorts System — Uninstall Script
# Usage: ./uninstall.sh <ssh_target>
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'
BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }

PROJECT_DIR="/opt/ai-shorts-system"
DOCKER_PROJECT="ai_shorts_system"
DOMAIN="video.theadityashah.com"
NGINX_CONF="ai-shorts-system"

SSH_TARGET="${1:-}"
if [[ -z "$SSH_TARGET" ]]; then
    echo "Usage: ./uninstall.sh <ssh_target>"
    exit 1
fi

rcmd() { ssh -o ConnectTimeout=10 "$SSH_TARGET" "$@"; }

echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
echo -e "${RED}${BOLD}  AI Shorts System — Uninstall${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════${NC}"
echo ""

read -p "This will stop containers and remove the project. Data volumes will be preserved. Continue? [y/N] " -n 1 -r
echo
[[ $REPLY =~ ^[Yy]$ ]] || exit 0

# Stop containers
info "Stopping containers..."
rcmd "cd ${PROJECT_DIR} 2>/dev/null && docker compose -p ${DOCKER_PROJECT} down 2>/dev/null" || true
ok "Containers stopped"

# Remove nginx config
info "Removing nginx config..."
rcmd "sudo rm -f /etc/nginx/sites-enabled/${NGINX_CONF} /etc/nginx/sites-available/${NGINX_CONF} 2>/dev/null" || true
rcmd "sudo nginx -t 2>/dev/null && sudo systemctl reload nginx 2>/dev/null" || true
ok "Nginx config removed"

# Remove project files (keep volumes)
info "Removing project files..."
rcmd "rm -rf ${PROJECT_DIR}" || rcmd "sudo rm -rf ${PROJECT_DIR}" || true
ok "Project files removed"

echo ""
echo -e "${YELLOW}Note: Docker volumes (ai_shorts_data, ai_shorts_n8n_data) are preserved.${NC}"
echo -e "${YELLOW}To remove volumes: ssh ${SSH_TARGET} 'docker volume rm ai_shorts_data ai_shorts_n8n_data'${NC}"
echo ""
ok "Uninstall complete"
