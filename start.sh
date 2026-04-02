#!/usr/bin/env bash
# SIGMAC - Script de arranque rapido
# Uso: bash start.sh

set -eu

echo ""
echo "  SIGMAC -- DNTT Bolivia"
echo "  Sistema de Inteligencia Geoespacial"
echo ""

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: Docker no esta instalado o no esta en el PATH."
  echo "Descarga Docker Desktop: https://docs.docker.com/get-docker/"
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker no esta corriendo."
  echo "Abre Docker Desktop y espera a que el icono de la ballena"
  echo "en la barra de tareas diga 'Engine running', luego vuelve a intentar."
  exit 1
fi

echo "[1/2] Construyendo y levantando servicios..."
docker compose up --build -d

echo "[2/2] Esperando servicios..."
sleep 12

echo ""
echo "  SIGMAC esta corriendo!"
echo ""
echo "  Dashboard  -->  http://localhost"
echo "  API Docs   -->  http://localhost/docs"
echo "  Health     -->  http://localhost/health"
echo ""
echo "  Usuario:    admin"
echo "  Contrasena: sigmac2024"
echo ""
echo "  Ver logs:  docker compose logs -f"
echo "  Detener:   docker compose down"
echo ""
