#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Run: sudo bash deploy/install-timeweb.sh shop.example.com'; exit 1; fi
domain=${1:?Pass the shop domain}
if [[ ! $domain =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ || $domain != *.* ]]; then
  echo 'Pass a DNS hostname, without https:// or path'; exit 1
fi
cd /opt/konspektum
test -f .env || { echo 'Create /opt/konspektum/.env first'; exit 1; }
apt-get update
apt-get install -y python3-venv python3-pip nginx certbot python3-certbot-nginx
id konspektum >/dev/null 2>&1 || useradd --system --home-dir /var/lib/konspektum --shell /usr/sbin/nologin konspektum
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip check
EXPECTED_SHOP_DOMAIN="$domain" .venv/bin/python -c 'import os; from konspektum.config import Config; c=Config.from_env(); expected="https://"+os.environ["EXPECTED_SHOP_DOMAIN"]; assert c.webapp_url==expected, "WEBAPP_URL must match the HTTPS domain passed to the installer"'
chown root:konspektum .env
chmod 640 .env
install -d -o konspektum -g konspektum -m 700 /var/lib/konspektum /var/lib/konspektum/backups
if [[ -f /var/lib/konspektum/konspektum.sqlite3 ]]; then
  chown konspektum:konspektum /var/lib/konspektum/konspektum.sqlite3
  chmod 600 /var/lib/konspektum/konspektum.sqlite3
fi
install -m 644 deploy/konspektum.service deploy/konspektum-backup.service deploy/konspektum-backup.timer /etc/systemd/system/
cat > /etc/nginx/sites-available/konspektum <<EOF
server {
    listen 80;
    server_name $domain;
    client_max_body_size 9m;
    access_log off;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 90s;
    }
}
EOF
ln -sfn /etc/nginx/sites-available/konspektum /etc/nginx/sites-enabled/konspektum
nginx -t
systemctl daemon-reload
systemctl enable --now konspektum konspektum-backup.timer
systemctl reload nginx
echo 'Next: sudo certbot --nginx -d' "$domain"
echo 'Inspect: sudo journalctl -u konspektum -n 50 --no-pager'
