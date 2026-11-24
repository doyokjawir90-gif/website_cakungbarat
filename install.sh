#!/usr/bin/env bash
# Pemasangan otomatis di VPS Ubuntu/Debian (jalankan sebagai root).
# Pemakaian:  sudo bash install.sh domain-anda.go.id email-anda@contoh.com
set -euo pipefail

DOMAIN="${1:-}"; EMAIL="${2:-}"
if [ "$(id -u)" -ne 0 ] || [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
  echo "Pemakaian: sudo bash install.sh domain-anda.go.id email-anda@contoh.com"; exit 1
fi
APP=/opt/cakung-barat
SRC="$(cd "$(dirname "$0")" && pwd)"

echo "==> Memasang kebutuhan sistem"
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv nginx certbot python3-certbot-nginx

echo "==> Menyalin aplikasi ke $APP"
id cakung >/dev/null 2>&1 || useradd -r -m -s /usr/sbin/nologin cakung
mkdir -p "$APP"
if [ "$SRC" != "$APP" ]; then
  cp -r "$SRC/app.py" "$SRC/static" "$SRC/requirements.txt" "$SRC/backup.sh" "$APP/"
fi
python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q -r "$APP/requirements.txt"

if [ ! -f "$APP/.env" ]; then
  cat > "$APP/.env" <<ENV
ADMIN_USERNAME=admin
ADMIN_PASSWORD=
COOKIE_SECURE=1
BEHIND_PROXY=1
ENV
fi
chown -R cakung:cakung "$APP"; chmod 600 "$APP/.env"; chmod +x "$APP/backup.sh"

echo "==> Membuat layanan sistem"
cat > /etc/systemd/system/cakung-barat.service <<UNIT
[Unit]
Description=Portal Kelurahan Cakung Barat
After=network.target
[Service]
User=cakung
Group=cakung
WorkingDirectory=$APP
EnvironmentFile=$APP/.env
ExecStart=$APP/venv/bin/gunicorn -w 3 -b 127.0.0.1:8000 --timeout 180 app:app
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now cakung-barat

echo "==> Mengatur Nginx"
cat > /etc/nginx/sites-available/cakung-barat <<NGX
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;
    client_max_body_size 110m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 180s;
        proxy_request_buffering off;
    }
}
NGX
ln -sf /etc/nginx/sites-available/cakung-barat /etc/nginx/sites-enabled/cakung-barat
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "==> Mengaktifkan HTTPS (domain harus sudah mengarah ke IP server ini)"
certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" -m "$EMAIL" --agree-tos --no-eff-email --redirect -n \
  || certbot --nginx -d "$DOMAIN" -m "$EMAIL" --agree-tos --no-eff-email --redirect -n

echo "==> Cadangan harian otomatis (pukul 02.00)"
( crontab -l 2>/dev/null | grep -v backup.sh; echo "0 2 * * * DATA_DIR=$APP/data $APP/backup.sh" ) | crontab -

sleep 2
echo
echo "======================================================"
echo " SELESAI. Buka: https://$DOMAIN"
echo " Dashboard: klik 'Masuk Admin' di pojok kanan atas."
if [ -f "$APP/data/AKUN_ADMIN_AWAL.txt" ]; then cat "$APP/data/AKUN_ADMIN_AWAL.txt"; fi
echo " Segera ganti password lalu hapus: $APP/data/AKUN_ADMIN_AWAL.txt"
echo "======================================================"
