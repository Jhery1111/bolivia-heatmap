# SIGMAC — Guía de despliegue en producción

Sistema de Información Geoespacial para la Monitorización y Análisis Climático de Bolivia.

---

## Requisitos previos

### Hardware mínimo recomendado
| Recurso | Mínimo | Recomendado |
|---------|--------|-------------|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 4 GB | 8 GB |
| Disco | 50 GB SSD | 100 GB SSD NVMe |
| Red | 100 Mbps | 1 Gbps |

### Software
- **Sistema operativo**: Ubuntu 22.04 LTS o Ubuntu 24.04 LTS (fresh install)
- **Acceso root** o usuario con privilegios `sudo`
- **Dominio DNS** apuntando a la IP del servidor (para certificado SSL)
- Puertos 80 y 443 accesibles desde Internet

---

## Paso 1 — Clonar el repositorio en /opt/sigmac

Clona el repositorio directamente en el directorio de producción:

```bash
sudo git clone https://github.com/dntt-bolivia/sigmac.git /opt/sigmac
cd /opt/sigmac
```

Verifica que la estructura existe antes de continuar:

```
/opt/sigmac/
├── api/            # FastAPI + api/main.py
├── etl/            # sync_job.py, queue_processor.py
├── analytics/      # módulos Python
├── frontend/       # código fuente Vite
├── db/             # scripts SQL (01_schema.sql, 02_extensions.sql, …)
└── deploy/         # scripts de despliegue (este directorio)
```

---

## Paso 2 — Preparar el archivo .env

Copia el archivo de ejemplo y edítalo con los valores de tu entorno:

```bash
sudo cp /opt/sigmac/.env.example /opt/sigmac/.env
sudo nano /opt/sigmac/.env
```

Variables obligatorias a revisar:

```dotenv
DOMAIN=tu-dominio.com
SECRET_KEY=<genera con: openssl rand -hex 32>
DATABASE_URL=postgresql://sigmac_user:<password>@127.0.0.1:5432/sigmac_db
REDIS_URL=redis://:<password>@127.0.0.1:6379/0
```

> **Nota**: El script `deploy_sigmac.sh` genera un `.env` automáticamente con credenciales aleatorias
> si no existe. Revisa y actualiza las credenciales generadas según tu entorno.

---

## Paso 3 — Ejecutar el script de instalación

El script gestiona toda la instalación de dependencias del sistema:

```bash
sudo bash /opt/sigmac/deploy/deploy_sigmac.sh tu-dominio.com
```

- Si no pasas el argumento del dominio, se usa `localhost` por defecto.
- El proceso tarda entre 5 y 15 minutos dependiendo de la velocidad de la red.
- Se genera un log completo en `/var/log/sigmac_deploy_YYYYMMDD_HHMMSS.log`.

El script instala y configura:
- PostgreSQL 16 + PostGIS 3 + pg_partman
- Redis (bind 127.0.0.1, maxmemory 256mb)
- Python 3.11 + entorno virtual `/opt/sigmac/venv`
- Node.js 20 LTS + build del frontend Vite
- Nginx con configuración de proxy y caché de tiles
- Unidades Systemd para API, ETL y Queue

---

## Paso 4 — Configurar SSL con Certbot

Una vez que el DNS apunta correctamente al servidor:

```bash
sudo certbot --nginx -d tu-dominio.com
```

Para renovación automática (ya incluida por certbot al instalar):

```bash
# Verificar el timer de renovación
sudo systemctl status certbot.timer

# Probar la renovación en modo dry-run
sudo certbot renew --dry-run
```

---

## Paso 5 — Configurar el firewall UFW

```bash
sudo bash /opt/sigmac/deploy/ufw_setup.sh
```

Si tu servidor usa un puerto SSH diferente al 22:

```bash
sudo bash /opt/sigmac/deploy/ufw_setup.sh 2222
```

El firewall bloquea acceso externo a PostgreSQL (5432), Redis (6379) y la API directa (8000).
Solo quedan abiertos los puertos 22/SSH, 80/HTTP y 443/HTTPS.

---

## Paso 6 — Cargar datos geográficos iniciales (GADM Bolivia)

Descarga el shapefile de Bolivia desde GADM:

```bash
# Descargar GADM Bolivia nivel administrativo 2 (municipios)
wget -P /tmp https://geodata.ucdavis.edu/gadm/gadm4.1/shp/gadm41_BOL_shp.zip
unzip /tmp/gadm41_BOL_shp.zip -d /tmp/gadm_bol/

# Importar a PostGIS (requiere shp2pgsql incluido en postgresql-client)
shp2pgsql -s 4326 -I /tmp/gadm_bol/gadm41_BOL_2.shp sigmac.bolivia_municipios \
    | PGPASSWORD="$(grep DB_PASS /opt/sigmac/.env | cut -d= -f2)" \
      psql -h 127.0.0.1 -U sigmac_user -d sigmac_db
```

> Fuente: [gadm.org](https://gadm.org/download_country.html) — Licencia: uso no comercial.
> Datos en WGS 84 (EPSG:4326) conforme a las convenciones del proyecto.

---

## Paso 7 — Verificar los servicios

```bash
# Estado de todos los servicios SIGMAC
sudo systemctl status sigmac-api sigmac-etl sigmac-queue

# Estado del timer de vistas materializadas
sudo systemctl status sigmac-db-refresh.timer

# Estado global
sudo systemctl list-units 'sigmac-*'
```

Salida esperada: todos los servicios en estado `active (running)`.

---

## Paso 8 — Monitorizar logs

```bash
# Log de la API en tiempo real
sudo journalctl -u sigmac-api -f

# Log del ETL
sudo journalctl -u sigmac-etl -f

# Log del Queue Processor
sudo journalctl -u sigmac-queue -f

# Últimas 100 líneas de todos los servicios SIGMAC
sudo journalctl -u 'sigmac-*' -n 100 --no-pager

# Logs de Nginx
sudo tail -f /var/log/nginx/sigmac_access.log
sudo tail -f /var/log/nginx/sigmac_error.log
```

---

## Paso 9 — Comandos de operación habituales

```bash
# Reiniciar la API (tras actualización de código)
sudo systemctl restart sigmac-api

# Recargar configuración Nginx sin downtime
sudo nginx -t && sudo systemctl reload nginx

# Conectar a la base de datos
sudo -u postgres psql -d sigmac_db

# Forzar refresco de vistas materializadas manualmente
sudo systemctl start sigmac-db-refresh.service

# Ver timers activos
sudo systemctl list-timers

# Actualizar código de la API sin reinicio completo
sudo systemctl reload sigmac-api   # envía SIGHUP → recarga workers uvicorn
```

---

## Paso 10 — URLs de acceso

| Recurso | URL |
|---------|-----|
| Dashboard | `https://tu-dominio.com/` |
| API REST | `https://tu-dominio.com/api/v1/` |
| Documentación API (Swagger) | `https://tu-dominio.com/api/v1/docs` |
| ReDoc | `https://tu-dominio.com/api/v1/redoc` |
| Health check | `https://tu-dominio.com/health` |

---

## Resolución de problemas

### La API no arranca

```bash
sudo journalctl -u sigmac-api -n 50 --no-pager
# Verificar que el .env está correctamente configurado
sudo cat /opt/sigmac/.env
# Probar manualmente
sudo -u sigmac /opt/sigmac/venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000
```

### Error de conexión a PostgreSQL

```bash
# Verificar estado
sudo systemctl status postgresql
# Probar conexión
PGPASSWORD="$(sudo grep DB_PASS /opt/sigmac/.env | cut -d= -f2)" \
    psql -h 127.0.0.1 -U sigmac_user -d sigmac_db -c "SELECT version();"
```

### Nginx devuelve 502 Bad Gateway

```bash
# Verificar que la API está escuchando en :8000
sudo ss -tlnp | grep 8000
# Revisar logs de Nginx
sudo tail -20 /var/log/nginx/sigmac_error.log
```

### Renovación SSL falla

```bash
# Verificar que los puertos 80/443 son accesibles desde Internet
# Renovar manualmente
sudo certbot renew --force-renewal
```

---

## Estructura de archivos de despliegue

```
deploy/
├── deploy_sigmac.sh          # Script principal de instalación
├── ufw_setup.sh              # Configuración firewall UFW
├── nginx/
│   └── sigmac.conf           # Configuración Nginx (HTTP→HTTPS + proxy)
├── systemd/
│   ├── sigmac-api.service    # Unidad API FastAPI/Uvicorn
│   ├── sigmac-etl.service    # Unidad ETL Sync Job
│   ├── sigmac-queue.service  # Unidad Queue Processor
│   ├── sigmac-db-refresh.service  # Unidad oneshot refresco vistas
│   └── sigmac-db-refresh.timer    # Timer cada 5 minutos
├── logrotate/
│   └── sigmac                # Configuración logrotate
└── README_DEPLOY.md          # Esta guía
```
