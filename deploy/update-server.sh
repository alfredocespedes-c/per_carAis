#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/home/acespedes/dev/per_carAis"
WEB_DIR="/var/www/per_carAis"
SERVICE_FILE="/etc/systemd/system/per-carais.service"
NGINX_FILE="/etc/nginx/sites-available/dev-prueba1.conaf.cl.conf"
NGINX_LINK="/etc/nginx/sites-enabled/dev-prueba1.conaf.cl.conf"

cd "$APP_DIR"

echo "[1/8] Actualizando repositorio..."
git pull origin main

echo "[2/8] Preparando entorno virtual..."
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
"$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

echo "[3/8] Instalando servicio systemd..."
sudo cp "$APP_DIR/deploy/per-carais.service" "$SERVICE_FILE"
sudo systemctl daemon-reload
sudo systemctl enable per-carais.service >/dev/null
sudo systemctl restart per-carais.service

echo "[4/8] Publicando frontend local..."
sudo mkdir -p "$WEB_DIR"
sudo rm -rf "$WEB_DIR"/*
sudo cp -r "$APP_DIR/frontend/." "$WEB_DIR/"
sudo chown -R www-data:www-data "$WEB_DIR"

echo "[5/8] Instalando configuración Nginx..."
sudo cp "$APP_DIR/deploy/dev-prueba1.conaf.cl.conf" "$NGINX_FILE"
sudo ln -sf "$NGINX_FILE" "$NGINX_LINK"
if [ -e /etc/nginx/sites-enabled/default ]; then
  sudo rm -f /etc/nginx/sites-enabled/default
fi

echo "[6/8] Validando y recargando Nginx..."
sudo nginx -t
sudo systemctl reload nginx

echo "[7/8] Verificando backend..."
for i in {1..20}; do
  if curl -fsS http://127.0.0.1:8301/api/health >/tmp/carais-health.json; then
    break
  fi
  sleep 1
done
cat /tmp/carais-health.json

echo
echo "[8/8] Verificando Nginx local..."
curl -fsS -H 'Host: dev-prueba1.conaf.cl' http://127.0.0.1/api/health

echo
echo "CarAis actualizado correctamente."
