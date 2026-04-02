#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  SIGMAC — Script de arranque rápido
#  Uso: bash start.sh
# ─────────────────────────────────────────────────────────────────────────────
set -eu

BOLD="\033[1m"
CYAN="\033[36m"
GREEN="\033[32m"
YELLOW="\033[33m"
RESET="\033[0m"

echo -e "${CYAN}${BOLD}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║   SIGMAC — DNTT Bolivia                      ║"
echo "  ║   Sistema de Inteligencia Geoespacial        ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${RESET}"

# ── Verificar Docker ──────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  echo -e "${YELLOW}[ERROR] Docker no está instalado.${RESET}"
  echo "  → Instala Docker Desktop: https://docs.docker.com/get-docker/"
  exit 1
fi

if ! docker compose version &>/dev/null; then
  echo -e "${YELLOW}[ERROR] Docker Compose v2 no encontrado.${RESET}"
  echo "  → Actualiza Docker Desktop o instala el plugin: https://docs.docker.com/compose/install/"
  exit 1
fi

# ── Crear .env.docker si no existe ───────────────────────────────────────────
if [ ! -f ".env.docker" ]; then
  echo -e "${YELLOW}[AVISO] .env.docker no encontrado — copiando desde plantilla...${RESET}"
  cp .env.docker .env.docker 2>/dev/null || true
fi

# ── Build + arranque ──────────────────────────────────────────────────────────
echo -e "${BOLD}[1/3] Construyendo imágenes Docker...${RESET}"
docker compose build --parallel

echo -e "${BOLD}[2/3] Levantando servicios...${RESET}"
docker compose up -d

echo -e "${BOLD}[3/3] Esperando que la API esté lista...${RESET}"
MAX=30
COUNT=0
until docker compose exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    &>/dev/null; do
  COUNT=$((COUNT+1))
  if [ "$COUNT" -ge "$MAX" ]; then
    echo -e "${YELLOW}[AVISO] La API tardó más de lo esperado. Revisa los logs:${RESET}"
    echo "  docker compose logs api"
    break
  fi
  echo -n "."
  sleep 2
done
echo ""

echo -e "${GREEN}${BOLD}"
echo "  ✔  SIGMAC está corriendo"
echo ""
echo "  Dashboard  →  http://localhost"
echo "  API Docs   →  http://localhost/docs"
echo "  Health     →  http://localhost/health"
echo ""
echo "  Usuario:    admin"
echo "  Contraseña: sigmac2024"
echo ""
echo "  Para ver logs:   docker compose logs -f"
echo "  Para detener:    docker compose down"
echo -e "${RESET}"
