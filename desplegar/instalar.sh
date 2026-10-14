#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Instalador de Nova para un VPS con Ubuntu/Debian.
#
# Deja Nova corriendo 24/7 con systemd (se reinicia solo si falla o si el VPS
# reinicia). Debe ejecutarse COMO ROOT dentro de la carpeta del proyecto:
#
#   sudo bash desplegar/instalar.sh
#
# Antes de correrlo, revisa que exista .env con tus credenciales reales.
# Al final imprime los comandos que te faltan (certificado HTTPS y webhook).
# ---------------------------------------------------------------------------
set -euo pipefail

RUTA_PROYECTO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USUARIO="nova"
SERVICIO="/etc/systemd/system/nova.service"

echo "==> Nova se instalara desde: ${RUTA_PROYECTO}"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: ejecuta este script como root:  sudo bash desplegar/instalar.sh"
    exit 1
fi

if [[ ! -f "${RUTA_PROYECTO}/.env" ]]; then
    echo "ERROR: no encuentro ${RUTA_PROYECTO}/.env"
    echo "       Copia .env.example a .env y rellena tus credenciales primero."
    exit 1
fi

echo "==> Instalando paquetes del sistema (python, nginx, certbot)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx

echo "==> Creando usuario de sistema '${USUARIO}' (si no existe)..."
if ! id -u "${USUARIO}" >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin "${USUARIO}"
fi

echo "==> Dando a '${USUARIO}' permiso sobre la carpeta del proyecto..."
chown -R "${USUARIO}:${USUARIO}" "${RUTA_PROYECTO}"

echo "==> Creando entorno virtual e instalando dependencias..."
sudo -u "${USUARIO}" python3 -m venv "${RUTA_PROYECTO}/.venv"
sudo -u "${USUARIO}" "${RUTA_PROYECTO}/.venv/bin/pip" install --upgrade pip
sudo -u "${USUARIO}" "${RUTA_PROYECTO}/.venv/bin/pip" install -r "${RUTA_PROYECTO}/requirements.txt"

echo "==> Generando el servicio systemd en ${SERVICIO}..."
sed \
    -e "s#/opt/nova#${RUTA_PROYECTO}#g" \
    -e "s#^User=nova#User=${USUARIO}#" \
    -e "s#^Group=nova#Group=${USUARIO}#" \
    "${RUTA_PROYECTO}/desplegar/nova.service" > "${SERVICIO}"

echo "==> Activar y arrancar Nova..."
systemctl daemon-reload
systemctl enable nova
systemctl restart nova

sleep 2
systemctl --no-pager --full status nova || true

cat <<'FIN'

-----------------------------------------------------------------------------
Nova quedo instalado y activo. Ultimos pasos:

1) Revisa los registros:
       journalctl -u nova -f

2) Apunta TU_DOMINIO a la IP de este VPS y publica el proxy HTTPS:
       sudo cp desplegar/nginx-nova.conf /etc/nginx/sites-available/nova
       sudo ln -s /etc/nginx/sites-available/nova /etc/nginx/sites-enabled/nova
       # edita TU_DOMINIO dentro de /etc/nginx/sites-available/nova
       sudo nginx -t && sudo systemctl reload nginx
       sudo certbot --nginx -d TU_DOMINIO --agree-tos -m TU_CORREO --redirect

3) En el .env pon la URL publica y reinicia:
       WEBHOOK_BASE_URL=https://TU_DOMINIO
       sudo systemctl restart nova

4) Registra el webhook de Telegram (una sola vez, o si cambia el dominio):
       sudo -u nova .venv/bin/python registrar_webhook.py

5) En Telegram: /estado  y luego  /llamar
-----------------------------------------------------------------------------
FIN