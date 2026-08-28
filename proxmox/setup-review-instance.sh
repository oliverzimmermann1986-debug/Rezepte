#!/usr/bin/env bash
# Run once inside a fresh LXC whose hostname is exactly "rezepte-review".
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/scrapper}"
APP_USER="${APP_USER:-scrapper}"
EXPECTED_HOST="rezepte-review"
REVERSE_PROXY_IP="${REVERSE_PROXY_IP:-192.168.1.141}"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Fehler: Bitte als root ausführen." >&2
  exit 1
fi
if [[ "$(hostname)" != "$EXPECTED_HOST" ]]; then
  echo "Fehler: Review-Setup läuft nur auf Hostname $EXPECTED_HOST." >&2
  exit 1
fi
if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
  echo "Fehler: Rezepte ist in $APP_DIR nicht installiert." >&2
  exit 1
fi

systemctl stop scrapper-web.service
systemctl disable --now scrapper-job.timer

"$APP_DIR/venv/bin/python" -m tools.setup_app_review_demo \
  --trusted-proxy-cidr "$REVERSE_PROXY_IP/32"

install -d -m 0755 /etc/scrapper
printf 'SCRAPPER_BIND_HOST=0.0.0.0\nSCRAPPER_FORWARDED_ALLOW_IPS=%s\n' \
  "$REVERSE_PROXY_IP" > /etc/scrapper/web.env
chown root:root /etc/scrapper/web.env
chmod 0600 /etc/scrapper/web.env

chown -R "$APP_USER:$APP_USER" \
  "$APP_DIR/data" "$APP_DIR/files/rezepte" "$APP_DIR/logs" "$APP_DIR/temp"
chmod 0600 "$APP_DIR/data/config.yaml"
systemctl restart scrapper-web.service

for _ in {1..30}; do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null; then
    echo "Review-Instanz ist bereit; Zugangsdaten: /root/rezepte-app-review-credentials.txt"
    exit 0
  fi
  sleep 1
done

journalctl -u scrapper-web.service -n 80 --no-pager >&2
exit 1
