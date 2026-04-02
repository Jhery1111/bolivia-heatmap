#!/usr/bin/env bash
# =============================================================================
# ufw_setup.sh — Configuración del firewall UFW para SIGMAC
# Uso: sudo bash deploy/ufw_setup.sh [ssh_port]
# ssh_port: puerto SSH personalizado (default: 22)
# =============================================================================
set -euo pipefail

# ── Colores ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── Verificar root ────────────────────────────────────────────────────────────
if [[ "${EUID}" -ne 0 ]]; then
    log_error "Este script debe ejecutarse como root: sudo bash $0"
    exit 1
fi

# Puerto SSH (arg $1 opcional, default 22)
SSH_PORT="${1:-22}"
log_info "Puerto SSH configurado: ${SSH_PORT}"

# ── Instalar UFW si no está presente ─────────────────────────────────────────
if ! command -v ufw &>/dev/null; then
    log_info "Instalando UFW..."
    apt-get install -y --no-install-recommends ufw
fi

# ── Verificar que SSH está activo antes de modificar el firewall ──────────────
if ! systemctl is-active --quiet ssh 2>/dev/null && \
   ! systemctl is-active --quiet sshd 2>/dev/null; then
    log_warn "El servicio SSH no está activo. Asegúrate de tener acceso físico antes de continuar."
fi

log_info "=== Configurando UFW ==="

# ── Reset y políticas por defecto ─────────────────────────────────────────────
ufw --force reset
log_ok "UFW reseteado a configuración limpia."

ufw default deny incoming
ufw default allow outgoing
log_ok "Política por defecto: DENY incoming / ALLOW outgoing."

# ── Reglas de entrada permitidas ─────────────────────────────────────────────

# SSH — restringido al puerto configurado
ufw allow "${SSH_PORT}/tcp" comment 'SSH'
log_ok "Permitido: SSH en puerto ${SSH_PORT}/tcp."

# HTTPS — tráfico de producción
ufw allow 443/tcp comment 'HTTPS'
log_ok "Permitido: HTTPS (443/tcp)."

# HTTP — solo para redireccion a HTTPS (certbot + redirect)
ufw allow 80/tcp comment 'HTTP hacia HTTPS redirect'
log_ok "Permitido: HTTP (80/tcp) — solo para redirección a HTTPS."

# ── Reglas de bloqueo explícito (defensa en profundidad) ──────────────────────

# PostgreSQL — solo accesible desde localhost; bloquear acceso externo
ufw deny 5432/tcp comment 'Bloquear PostgreSQL externo'
log_ok "Bloqueado: PostgreSQL (5432/tcp) desde exterior."

# Redis — solo accesible desde localhost; bloquear acceso externo
ufw deny 6379/tcp comment 'Bloquear Redis externo'
log_ok "Bloqueado: Redis (6379/tcp) desde exterior."

# API uvicorn — el acceso debe ser solo via Nginx; bloquear acceso directo externo
ufw deny 8000/tcp comment 'Bloquear acceso directo API uvicorn'
log_ok "Bloqueado: API directa (8000/tcp) desde exterior."

# ── Protección contra ataques comunes ─────────────────────────────────────────

# Limitar intentos de conexión SSH para mitigar fuerza bruta
ufw limit "${SSH_PORT}/tcp" comment 'Rate limit SSH'
log_ok "Rate limiting aplicado al puerto SSH (${SSH_PORT}/tcp)."

# ── Habilitar UFW ─────────────────────────────────────────────────────────────
ufw --force enable
log_ok "UFW habilitado."

# ── Verificar estado final ────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}══════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Estado de UFW:${NC}"
echo -e "${BLUE}══════════════════════════════════════════════${NC}"
ufw status verbose

echo ""
log_ok "UFW configurado correctamente para SIGMAC."
echo ""
echo -e "${YELLOW}Resumen de reglas:${NC}"
echo -e "  PERMITIDO:   SSH   ${SSH_PORT}/tcp  (con rate limiting)"
echo -e "  PERMITIDO:   HTTPS 443/tcp"
echo -e "  PERMITIDO:   HTTP  80/tcp   (redirect a HTTPS)"
echo -e "  BLOQUEADO:   PostgreSQL 5432/tcp (solo localhost)"
echo -e "  BLOQUEADO:   Redis      6379/tcp (solo localhost)"
echo -e "  BLOQUEADO:   API        8000/tcp (acceso via Nginx únicamente)"
echo ""
echo -e "${YELLOW}Nota:${NC} Para acceso de administración adicional (Adminer, Grafana, etc.),"
echo -e "usa un túnel SSH: ssh -L 5432:127.0.0.1:5432 usuario@servidor"
