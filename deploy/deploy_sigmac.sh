#!/usr/bin/env bash
# =============================================================================
# deploy_sigmac.sh — Instalación automatizada de SIGMAC en Ubuntu 22.04/24.04
# Uso: sudo bash deploy/deploy_sigmac.sh [dominio.com]
# =============================================================================
set -euo pipefail

# ── Colores para output ────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_section() { echo -e "\n${BLUE}════════════════════════════════════════${NC}"; \
                echo -e "${BLUE}  $*${NC}"; \
                echo -e "${BLUE}════════════════════════════════════════${NC}"; }

# =============================================================================
# SECCIÓN 1: VERIFICACIÓN
# =============================================================================
log_section "Verificación de requisitos"

# Debe ejecutarse como root
if [[ "${EUID}" -ne 0 ]]; then
    log_error "Este script debe ejecutarse como root."
    log_error "Uso: sudo bash deploy/deploy_sigmac.sh [dominio.com]"
    exit 1
fi
log_ok "Ejecutando como root."

# Verificar Ubuntu 22.04 o 24.04
if [[ ! -f /etc/os-release ]]; then
    log_error "No se puede determinar el sistema operativo."
    exit 1
fi
source /etc/os-release
if [[ "${ID}" != "ubuntu" ]]; then
    log_error "Este script requiere Ubuntu. Sistema detectado: ${ID}"
    exit 1
fi
if [[ "${VERSION_ID}" != "22.04" && "${VERSION_ID}" != "24.04" ]]; then
    log_error "Se requiere Ubuntu 22.04 o 24.04. Versión detectada: ${VERSION_ID}"
    exit 1
fi
log_ok "Sistema operativo: Ubuntu ${VERSION_ID} LTS."

# Verificar acceso a internet
log_info "Verificando acceso a internet..."
if ! curl -fsSL --max-time 10 https://www.google.com -o /dev/null 2>/dev/null; then
    log_error "Sin acceso a internet. Verifica la conectividad de red."
    exit 1
fi
log_ok "Acceso a internet verificado."

# =============================================================================
# SECCIÓN 2: VARIABLES
# =============================================================================
log_section "Configuración de variables"

SIGMAC_USER="sigmac"
SIGMAC_DIR="/opt/sigmac"
DB_NAME="sigmac_db"
DB_USER="sigmac_user"
DB_PASS="$(openssl rand -base64 32 | tr -d '=+/' | head -c 32)"
REDIS_PASS="$(openssl rand -base64 24 | tr -d '=+/' | head -c 24)"
SECRET_KEY="$(openssl rand -hex 32)"
DOMAIN="${1:-localhost}"
PG_VERSION="16"
PYTHON_BIN="python3.11"
VENV_DIR="${SIGMAC_DIR}/venv"
DEPLOY_LOG="/var/log/sigmac_deploy_$(date +%Y%m%d_%H%M%S).log"

# Redirigir stdout y stderr también al log
exec > >(tee -a "${DEPLOY_LOG}") 2>&1

log_ok "Variables configuradas:"
log_info "  SIGMAC_DIR   = ${SIGMAC_DIR}"
log_info "  DB_NAME      = ${DB_NAME}"
log_info "  DB_USER      = ${DB_USER}"
log_info "  DOMAIN       = ${DOMAIN}"
log_info "  Deploy log   = ${DEPLOY_LOG}"

# =============================================================================
# SECCIÓN 3: ACTUALIZACIÓN DEL SISTEMA
# =============================================================================
log_section "Actualización del sistema"

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y --no-install-recommends
apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    ca-certificates \
    lsb-release \
    apt-transport-https \
    software-properties-common \
    wget \
    unzip \
    git \
    openssl
log_ok "Sistema actualizado y dependencias base instaladas."

# =============================================================================
# SECCIÓN 4: INSTALACIÓN PostgreSQL 16 + PostGIS 3
# =============================================================================
log_section "Instalación de PostgreSQL 16 + PostGIS 3"

# Agregar repositorio oficial PGDG
PGDG_KEY="/usr/share/keyrings/postgresql-archive-keyring.gpg"
if [[ ! -f "${PGDG_KEY}" ]]; then
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        | gpg --dearmor -o "${PGDG_KEY}"
    log_ok "Clave GPG de PGDG instalada."
fi

PGDG_LIST="/etc/apt/sources.list.d/pgdg.list"
CODENAME="$(lsb_release -cs)"
echo "deb [signed-by=${PGDG_KEY}] https://apt.postgresql.org/pub/repos/apt ${CODENAME}-pgdg main" \
    > "${PGDG_LIST}"
log_ok "Repositorio PGDG agregado (${CODENAME}-pgdg)."

apt-get update -y
apt-get install -y --no-install-recommends \
    "postgresql-${PG_VERSION}" \
    "postgresql-client-${PG_VERSION}" \
    "postgresql-${PG_VERSION}-postgis-3" \
    "postgresql-${PG_VERSION}-postgis-3-scripts" \
    "postgresql-${PG_VERSION}-partman"
log_ok "PostgreSQL ${PG_VERSION}, PostGIS 3 y pg_partman instalados."

systemctl enable --now "postgresql@${PG_VERSION}-main" || systemctl enable --now postgresql
log_ok "Servicio PostgreSQL habilitado y arrancado."

# =============================================================================
# SECCIÓN 5: INSTALACIÓN REDIS
# =============================================================================
log_section "Instalación y configuración de Redis"

apt-get install -y --no-install-recommends redis-server
log_ok "Redis instalado."

REDIS_CONF="/etc/redis/redis.conf"
# Configurar maxmemory y política de evicción
sed -i 's/^# maxmemory .*/maxmemory 256mb/' "${REDIS_CONF}"
sed -i 's/^maxmemory .*/maxmemory 256mb/' "${REDIS_CONF}"
# Si no existe la directiva, agregarla
grep -q "^maxmemory " "${REDIS_CONF}" || echo "maxmemory 256mb" >> "${REDIS_CONF}"

sed -i 's/^# maxmemory-policy .*/maxmemory-policy allkeys-lru/' "${REDIS_CONF}"
sed -i 's/^maxmemory-policy .*/maxmemory-policy allkeys-lru/' "${REDIS_CONF}"
grep -q "^maxmemory-policy " "${REDIS_CONF}" || echo "maxmemory-policy allkeys-lru" >> "${REDIS_CONF}"

# Configurar contraseña de Redis
sed -i "s/^# requirepass .*/requirepass ${REDIS_PASS}/" "${REDIS_CONF}"
grep -q "^requirepass " "${REDIS_CONF}" || echo "requirepass ${REDIS_PASS}" >> "${REDIS_CONF}"

# Bind solo a localhost
sed -i 's/^bind .*/bind 127.0.0.1 -::1/' "${REDIS_CONF}"
grep -q "^bind " "${REDIS_CONF}" || echo "bind 127.0.0.1" >> "${REDIS_CONF}"

systemctl enable --now redis-server
log_ok "Redis configurado (maxmemory=256mb, allkeys-lru, bind=127.0.0.1)."

# =============================================================================
# SECCIÓN 6: INSTALACIÓN PYTHON 3.11
# =============================================================================
log_section "Instalación de Python 3.11"

# En Ubuntu 22.04 puede requerir PPA; en 24.04 está en los repos oficiales
if ! apt-get install -y --no-install-recommends python3.11 python3.11-venv python3.11-dev 2>/dev/null; then
    log_warn "python3.11 no encontrado en repos oficiales, agregando PPA deadsnakes..."
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -y
    apt-get install -y --no-install-recommends python3.11 python3.11-venv python3.11-dev
fi

apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libssl-dev \
    libffi-dev \
    python3-pip

# Instalar pip para python3.11
curl -fsSL https://bootstrap.pypa.io/get-pip.py | python3.11 - --quiet
log_ok "Python 3.11 instalado: $(python3.11 --version)"

# =============================================================================
# SECCIÓN 7: INSTALACIÓN NODE.JS 20 LTS
# =============================================================================
log_section "Instalación de Node.js 20 LTS"

curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y --no-install-recommends nodejs
log_ok "Node.js instalado: $(node --version), npm: $(npm --version)"

# =============================================================================
# SECCIÓN 8: INSTALACIÓN NGINX
# =============================================================================
log_section "Instalación de Nginx"

apt-get install -y --no-install-recommends nginx
systemctl enable --now nginx
log_ok "Nginx instalado y arrancado."

# Crear directorio de caché de tiles
mkdir -p /var/cache/nginx/sigmac_tiles
chown www-data:www-data /var/cache/nginx/sigmac_tiles
log_ok "Directorio de caché Nginx creado: /var/cache/nginx/sigmac_tiles"

# =============================================================================
# SECCIÓN 9: CREACIÓN DEL USUARIO DEL SISTEMA
# =============================================================================
log_section "Creación del usuario de sistema 'sigmac'"

if ! id -u "${SIGMAC_USER}" &>/dev/null; then
    useradd \
        --system \
        --shell /sbin/nologin \
        --home "${SIGMAC_DIR}" \
        --no-create-home \
        "${SIGMAC_USER}"
    log_ok "Usuario de sistema '${SIGMAC_USER}' creado."
else
    log_warn "Usuario '${SIGMAC_USER}' ya existe, omitiendo creación."
fi

# Crear directorios de la aplicación si no existen
mkdir -p "${SIGMAC_DIR}"/{api,etl,analytics,frontend,db,logs}
log_ok "Estructura de directorios creada en ${SIGMAC_DIR}."

# =============================================================================
# SECCIÓN 10: CONFIGURACIÓN POSTGRESQL
# =============================================================================
log_section "Configuración de PostgreSQL"

PG_HBA="/etc/postgresql/${PG_VERSION}/main/pg_hba.conf"
PG_CONF="/etc/postgresql/${PG_VERSION}/main/postgresql.conf"

# Optimizaciones básicas de postgresql.conf
cat >> "${PG_CONF}" <<EOF

# ── SIGMAC tuning ─────────────────────────────────────────────────────────────
max_connections = 100
shared_buffers = 256MB
effective_cache_size = 1GB
maintenance_work_mem = 64MB
checkpoint_completion_target = 0.9
wal_buffers = 16MB
default_statistics_target = 100
random_page_cost = 1.1
effective_io_concurrency = 200
work_mem = 4MB
min_wal_size = 1GB
max_wal_size = 4GB
EOF
log_ok "Parámetros de rendimiento agregados a postgresql.conf."

# Crear usuario y base de datos
log_info "Creando base de datos y usuario PostgreSQL..."
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '${DB_USER}') THEN
        CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASS}';
    ELSE
        ALTER ROLE ${DB_USER} WITH PASSWORD '${DB_PASS}';
    END IF;
END
\$\$;

SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec

GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
SQL
log_ok "Rol '${DB_USER}' y base de datos '${DB_NAME}' listos."

# Habilitar extensiones PostGIS y partman
sudo -u postgres psql -d "${DB_NAME}" -v ON_ERROR_STOP=1 <<SQL
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;
CREATE EXTENSION IF NOT EXISTS pg_partman;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gist;
SQL
log_ok "Extensiones PostgreSQL habilitadas (postgis, postgis_topology, pg_partman, pg_trgm, btree_gist)."

# Configurar pg_hba.conf para que sigmac_user acceda con scram-sha-256 desde localhost
# Insertar la regla antes de la primera línea de acceso local existente
if ! grep -q "sigmac_user" "${PG_HBA}"; then
    sed -i "/^local\s\+all\s\+all/i local   ${DB_NAME}   ${DB_USER}   scram-sha-256\nhost    ${DB_NAME}   ${DB_USER}   127.0.0.1/32   scram-sha-256\nhost    ${DB_NAME}   ${DB_USER}   ::1/128        scram-sha-256" "${PG_HBA}"
    log_ok "Reglas de pg_hba.conf agregadas para ${DB_USER}."
fi

# Recargar configuración de PostgreSQL
systemctl reload "postgresql@${PG_VERSION}-main" 2>/dev/null \
    || systemctl reload postgresql \
    || pg_ctlcluster "${PG_VERSION}" main reload
log_ok "Configuración PostgreSQL recargada."

# Ejecutar scripts SQL de inicialización en orden
if [[ -d "${SIGMAC_DIR}/db" ]]; then
    log_info "Ejecutando scripts SQL de inicialización..."
    for sql_file in $(ls -v "${SIGMAC_DIR}/db/"[0-9][0-9]_*.sql 2>/dev/null | sort); do
        log_info "  → Ejecutando: $(basename "${sql_file}")"
        PGPASSWORD="${DB_PASS}" psql \
            -h 127.0.0.1 \
            -U "${DB_USER}" \
            -d "${DB_NAME}" \
            -f "${sql_file}" \
            -v ON_ERROR_STOP=1
        log_ok "  ✓ $(basename "${sql_file}") ejecutado."
    done
else
    log_warn "Directorio ${SIGMAC_DIR}/db no encontrado, omitiendo scripts SQL."
fi

# =============================================================================
# SECCIÓN 11: SETUP PYTHON VENV
# =============================================================================
log_section "Configuración del entorno virtual Python"

log_info "Creando venv en ${VENV_DIR}..."
"${PYTHON_BIN}" -m venv "${VENV_DIR}"
log_ok "Venv creado con Python: $(${VENV_DIR}/bin/python --version)"

# Actualizar pip, setuptools y wheel
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip setuptools wheel

# Instalar dependencias de cada módulo si existe requirements.txt
for req_dir in api etl analytics; do
    REQ_FILE="${SIGMAC_DIR}/${req_dir}/requirements.txt"
    if [[ -f "${REQ_FILE}" ]]; then
        log_info "Instalando dependencias de ${req_dir}/requirements.txt..."
        "${VENV_DIR}/bin/pip" install --quiet -r "${REQ_FILE}"
        log_ok "Dependencias de ${req_dir} instaladas."
    else
        log_warn "${REQ_FILE} no encontrado, omitiendo."
    fi
done

# =============================================================================
# SECCIÓN 12: BUILD FRONTEND
# =============================================================================
log_section "Build del frontend Vite"

FRONTEND_DIR="${SIGMAC_DIR}/frontend"
if [[ -f "${FRONTEND_DIR}/package.json" ]]; then
    log_info "Instalando dependencias npm..."
    cd "${FRONTEND_DIR}"
    npm install --silent
    log_info "Ejecutando npm run build..."
    npm run build
    log_ok "Frontend construido en ${FRONTEND_DIR}/dist."
    cd /
else
    log_warn "${FRONTEND_DIR}/package.json no encontrado, omitiendo build frontend."
    mkdir -p "${FRONTEND_DIR}/dist"
    cat > "${FRONTEND_DIR}/dist/index.html" <<'HTML'
<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><title>SIGMAC — Iniciando</title></head>
<body><h1>SIGMAC</h1><p>Frontend pendiente de despliegue.</p></body>
</html>
HTML
fi

# =============================================================================
# SECCIÓN 13: ARCHIVO .env
# =============================================================================
log_section "Generación del archivo .env"

ENV_FILE="${SIGMAC_DIR}/.env"
cat > "${ENV_FILE}" <<EOF
# ── SIGMAC Environment — generado por deploy_sigmac.sh $(date '+%Y-%m-%d %H:%M:%S') ──

# Aplicación
ENVIRONMENT=production
DEBUG=false
SECRET_KEY=${SECRET_KEY}
DOMAIN=${DOMAIN}
ALLOWED_HOSTS=${DOMAIN},localhost,127.0.0.1

# Base de datos PostgreSQL
DATABASE_URL=postgresql://${DB_USER}:${DB_PASS}@127.0.0.1:5432/${DB_NAME}
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=${DB_NAME}
DB_USER=${DB_USER}
DB_PASS=${DB_PASS}

# Redis
REDIS_URL=redis://:${REDIS_PASS}@127.0.0.1:6379/0
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=${REDIS_PASS}

# API
API_HOST=127.0.0.1
API_PORT=8000
API_WORKERS=4

# Logs
LOG_LEVEL=INFO
LOG_DIR=/var/log/sigmac
EOF

chmod 640 "${ENV_FILE}"
log_ok "Archivo .env generado en ${ENV_FILE}."

# =============================================================================
# SECCIÓN 14: ARCHIVOS SYSTEMD
# =============================================================================
log_section "Instalación de unidades Systemd"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_SRC="${SCRIPT_DIR}/systemd"
SYSTEMD_DEST="/etc/systemd/system"

for unit_file in sigmac-api.service sigmac-etl.service sigmac-queue.service \
                 sigmac-db-refresh.service sigmac-db-refresh.timer; do
    if [[ -f "${SYSTEMD_SRC}/${unit_file}" ]]; then
        cp "${SYSTEMD_SRC}/${unit_file}" "${SYSTEMD_DEST}/${unit_file}"
        log_ok "  ${unit_file} → ${SYSTEMD_DEST}/"
    else
        log_warn "  ${unit_file} no encontrado en ${SYSTEMD_SRC}, omitiendo."
    fi
done

# =============================================================================
# SECCIÓN 15: CONFIGURACIÓN NGINX
# =============================================================================
log_section "Configuración de Nginx"

NGINX_CONF_SRC="${SCRIPT_DIR}/nginx/sigmac.conf"
NGINX_CONF_DEST="/etc/nginx/sites-available/sigmac"
NGINX_ENABLED="/etc/nginx/sites-enabled/sigmac"

if [[ -f "${NGINX_CONF_SRC}" ]]; then
    # Sustituir la variable DOMAIN en el archivo de configuración
    sed "s/\${DOMAIN}/${DOMAIN}/g" "${NGINX_CONF_SRC}" > "${NGINX_CONF_DEST}"
    ln -sf "${NGINX_CONF_DEST}" "${NGINX_ENABLED}"
    # Deshabilitar el sitio default
    rm -f /etc/nginx/sites-enabled/default
    log_ok "Configuración Nginx instalada para dominio: ${DOMAIN}"
    nginx -t && log_ok "Sintaxis Nginx: OK." || log_warn "Verifica la configuración Nginx manualmente."
else
    log_warn "nginx/sigmac.conf no encontrado, omitiendo configuración Nginx."
fi

# =============================================================================
# SECCIÓN 16: LOGROTATE
# =============================================================================
log_section "Configuración de logrotate"

mkdir -p /var/log/sigmac
chown "${SIGMAC_USER}:${SIGMAC_USER}" /var/log/sigmac
chmod 750 /var/log/sigmac

LOGROTATE_SRC="${SCRIPT_DIR}/logrotate/sigmac"
if [[ -f "${LOGROTATE_SRC}" ]]; then
    cp "${LOGROTATE_SRC}" /etc/logrotate.d/sigmac
    log_ok "Configuración logrotate instalada."
else
    log_warn "logrotate/sigmac no encontrado, omitiendo."
fi

# =============================================================================
# SECCIÓN 17: PERMISOS FINALES
# =============================================================================
log_section "Ajuste de permisos"

chown -R "${SIGMAC_USER}:${SIGMAC_USER}" "${SIGMAC_DIR}"
# El .env solo legible por sigmac y root
chmod 640 "${SIGMAC_DIR}/.env"
# Scripts de Python ejecutables
find "${SIGMAC_DIR}" -name "*.py" -exec chmod 644 {} \;
find "${SIGMAC_DIR}/venv/bin" -type f -exec chmod 755 {} \;
log_ok "Permisos ajustados: ${SIGMAC_DIR} → ${SIGMAC_USER}:${SIGMAC_USER}"

# =============================================================================
# SECCIÓN 18: HABILITAR Y ARRANCAR SERVICIOS
# =============================================================================
log_section "Habilitación y arranque de servicios"

systemctl daemon-reload
log_ok "systemctl daemon-reload ejecutado."

SERVICES=("sigmac-api" "sigmac-etl" "sigmac-queue")
for svc in "${SERVICES[@]}"; do
    if [[ -f "/etc/systemd/system/${svc}.service" ]]; then
        systemctl enable --now "${svc}" \
            && log_ok "${svc}: habilitado y arrancado." \
            || log_warn "${svc}: no se pudo arrancar (verifica los logs con: journalctl -u ${svc} -n 50)."
    else
        log_warn "Unidad ${svc}.service no instalada, omitiendo."
    fi
done

# Habilitar timer de refresco de vistas materializadas
if [[ -f "/etc/systemd/system/sigmac-db-refresh.timer" ]]; then
    systemctl enable --now sigmac-db-refresh.timer \
        && log_ok "sigmac-db-refresh.timer: habilitado." \
        || log_warn "sigmac-db-refresh.timer: no se pudo habilitar."
fi

# Recargar Nginx con la nueva configuración
systemctl reload nginx && log_ok "Nginx recargado." || log_warn "Nginx no recargó correctamente."

# =============================================================================
# SECCIÓN 19: VERIFICACIÓN DE SERVICIOS
# =============================================================================
log_section "Verificación de servicios"

ALL_OK=true

check_service() {
    local svc="$1"
    if [[ ! -f "/etc/systemd/system/${svc}.service" ]]; then
        log_warn "  ${svc}: unidad no instalada."
        return
    fi
    if systemctl is-active --quiet "${svc}"; then
        log_ok "  ${svc}: ACTIVO"
    else
        log_warn "  ${svc}: INACTIVO (revisa: journalctl -u ${svc} -n 30)"
        ALL_OK=false
    fi
}

check_service "sigmac-api"
check_service "sigmac-etl"
check_service "sigmac-queue"

# Verificar PostgreSQL
if systemctl is-active --quiet "postgresql@${PG_VERSION}-main" 2>/dev/null \
   || systemctl is-active --quiet postgresql 2>/dev/null; then
    log_ok "  postgresql: ACTIVO"
else
    log_warn "  postgresql: INACTIVO"
    ALL_OK=false
fi

# Verificar Redis
if systemctl is-active --quiet redis-server; then
    log_ok "  redis-server: ACTIVO"
else
    log_warn "  redis-server: INACTIVO"
    ALL_OK=false
fi

# Verificar Nginx
if systemctl is-active --quiet nginx; then
    log_ok "  nginx: ACTIVO"
else
    log_warn "  nginx: INACTIVO"
    ALL_OK=false
fi

# Health check rápido de la API (si está activa)
sleep 2
if curl -fsSL --max-time 5 http://127.0.0.1:8000/health -o /dev/null 2>/dev/null; then
    log_ok "  API health check: OK (http://127.0.0.1:8000/health)"
else
    log_warn "  API health check: no responde aún (puede tardar unos segundos en iniciar)."
fi

# =============================================================================
# SECCIÓN 20: OUTPUT FINAL
# =============================================================================
log_section "Despliegue completado"

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║          SIGMAC — Despliegue completado              ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BLUE}URLs de acceso:${NC}"
if [[ "${DOMAIN}" == "localhost" ]]; then
    echo -e "  Dashboard:   ${GREEN}http://localhost/${NC}"
    echo -e "  API:         ${GREEN}http://localhost/api/v1/${NC}"
    echo -e "  Health:      ${GREEN}http://localhost/health${NC}"
else
    echo -e "  Dashboard:   ${GREEN}https://${DOMAIN}/${NC}"
    echo -e "  API:         ${GREEN}https://${DOMAIN}/api/v1/${NC}"
    echo -e "  Health:      ${GREEN}https://${DOMAIN}/health${NC}"
fi

echo ""
echo -e "${BLUE}Credenciales de base de datos (guárdalas en lugar seguro):${NC}"
echo -e "  Host:        127.0.0.1:5432"
echo -e "  Base datos:  ${YELLOW}${DB_NAME}${NC}"
echo -e "  Usuario:     ${YELLOW}${DB_USER}${NC}"
echo -e "  Contraseña:  ${YELLOW}${DB_PASS}${NC}"
echo -e "  DATABASE_URL postgresql://${DB_USER}:${DB_PASS}@127.0.0.1:5432/${DB_NAME}"

echo ""
echo -e "${BLUE}Redis:${NC}"
echo -e "  URL:         redis://:${REDIS_PASS}@127.0.0.1:6379/0"

echo ""
echo -e "${BLUE}Próximos pasos:${NC}"
echo -e "  1. Configurar SSL:  ${YELLOW}certbot --nginx -d ${DOMAIN}${NC}"
echo -e "  2. Revisar logs:    ${YELLOW}journalctl -u sigmac-api -f${NC}"
echo -e "  3. Estado general:  ${YELLOW}systemctl status sigmac-*${NC}"
echo -e "  4. Log de deploy:   ${YELLOW}${DEPLOY_LOG}${NC}"

if [[ "${ALL_OK}" == "true" ]]; then
    echo ""
    log_ok "Todos los servicios están operativos. SIGMAC listo para producción."
else
    echo ""
    log_warn "Algunos servicios requieren atención. Consulta los logs indicados arriba."
fi

echo ""
