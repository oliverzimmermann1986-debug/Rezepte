#!/usr/bin/env bash
# ============================================================
# In-Container Installation
# Führe das innerhalb des LXC Containers aus.
# ============================================================
set -euo pipefail

APP_DIR="/opt/scrapper"
APP_USER="scrapper"
REPO_URL="${REPO_URL:-https://github.com/oliverzimmermann1986-debug/Rezepte.git}"
BRANCH="${BRANCH:-main}"

echo "▶️  Rezepte-Installation startet..."

# 1. System aktualisieren
echo "📦 System Update..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y upgrade
apt-get install -y --no-install-recommends \
  ca-certificates curl wget gnupg git \
  python3 python3-venv python3-pip python3-dev \
  build-essential ffmpeg \
  tesseract-ocr tesseract-ocr-osd tesseract-ocr-deu tesseract-ocr-eng \
  sqlite3 \
  cron \
  policykit-1 \
  sudo \
  tzdata

# 2. Zeitzone
ln -sf /usr/share/zoneinfo/Europe/Berlin /etc/localtime
echo "Europe/Berlin" > /etc/timezone

# 3. App-User anlegen
if ! id "$APP_USER" >/dev/null 2>&1; then
  echo "👤 Erstelle User $APP_USER"
  useradd -r -m -d /home/$APP_USER -s /bin/bash $APP_USER
fi

# 4. Repository klonen. Bestehende Installationen werden aus einem frischen
# Release-Staging heraus mit Rollback und gestoppten Diensten aktualisiert.
if [[ ! -d "$APP_DIR" ]]; then
  echo "📥 Klone Repository..."
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
else
  echo "🔄 Bestehende Installation atomar aktualisieren..."
  UPDATE_STAGE="$(mktemp -d /tmp/rezepte-release.XXXXXX)"
  trap 'rm -rf -- "$UPDATE_STAGE"' EXIT
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$UPDATE_STAGE"
  APP_DIR="$APP_DIR" APP_USER="$APP_USER" \
    bash "$UPDATE_STAGE/proxmox/update-local.sh"
  exit 0
fi

cd "$APP_DIR"

# 5. Python Virtual Environment
echo "🐍 Python venv..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip wheel
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# Headless Chromium für TikTok-Captions, die erst nach Klick auf "mehr"
# gerendert werden. Ein fixer Pfad macht den Browser für den Service-User
# verfügbar, obwohl die Installation als root läuft.
PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/playwright-browsers" \
  "$APP_DIR/venv/bin/python" -m playwright install --with-deps chromium

# 6. Verzeichnisse anlegen.
# Default-Ablage für sortierte Videos liegt INNERHALB des Containers
# unter /opt/scrapper/files/. Wenn du z.B. einen Bind-Mount willst,
# editier nach der Installation einfach data/config.yaml -> paths:
# und passe das Verzeichnis an deinen Mount an.
mkdir -p "$APP_DIR/data" "$APP_DIR/logs" "$APP_DIR/temp" \
         "$APP_DIR/files/rezepte" "$APP_DIR/files/hochzeit"

# 7. Default-Config erstellen wenn fehlt
if [[ ! -f "$APP_DIR/data/config.yaml" ]]; then
  echo "📝 Erstelle Default-Config mit zufälligem Passwort + Secret..."
  cp "$APP_DIR/config/config.example.yaml" "$APP_DIR/data/config.yaml"

  # Keine tr|head-Pipeline unter pipefail: head beendet tr per SIGPIPE und
  # konnte dadurch Neuinstallationen mit Exit 141 abbrechen.
  GEN_PASS=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
  GEN_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')

  # Default-Platzhalter durch zufällige Werte ersetzen (sed -i, sauber escaped)
  sed -i "s|password: changeme|password: ${GEN_PASS}|" "$APP_DIR/data/config.yaml"
  sed -i "s|secret_key: change-this-to-random-string-32chars-min|secret_key: ${GEN_SECRET}|" "$APP_DIR/data/config.yaml"

  chown $APP_USER:$APP_USER "$APP_DIR/data/config.yaml"
  chmod 600 "$APP_DIR/data/config.yaml"

  # Generiertes Passwort separat ablegen, damit es nicht im Terminal-Scrollback hängt
  echo "$GEN_PASS" > "$APP_DIR/data/.initial-password"
  chown $APP_USER:$APP_USER "$APP_DIR/data/.initial-password"
  chmod 600 "$APP_DIR/data/.initial-password"

  INITIAL_PASSWORD="$GEN_PASS"
fi

# 8. systemd Services installieren
echo "⚙️  Installiere systemd Units..."
install -m 0644 "$APP_DIR/systemd/scrapper-web.service" /etc/systemd/system/
install -m 0644 "$APP_DIR/systemd/scrapper-job.service" /etc/systemd/system/
install -m 0644 "$APP_DIR/systemd/scrapper-job.timer" /etc/systemd/system/
install -m 0644 "$APP_DIR/systemd/scrapper-db-backup.service" /etc/systemd/system/
install -m 0644 "$APP_DIR/systemd/scrapper-db-backup.timer" /etc/systemd/system/
install -m 0644 "$APP_DIR/systemd/scrapper-schedule-apply.service" /etc/systemd/system/
install -d -m 0755 /etc/systemd/system/scrapper-job.timer.d
install -d -m 0755 /etc/scrapper
if [[ -f "$APP_DIR/data/web.env" && ! -f /etc/scrapper/web.env ]]; then
  install -m 0600 -o root -g root "$APP_DIR/data/web.env" /etc/scrapper/web.env
fi

# Polkit erlaubt dem Webdienst nur den erneut validierenden root-Helper.
install -m 0644 "$APP_DIR/systemd/49-scrapper-systemctl.rules" \
  /etc/polkit-1/rules.d/49-scrapper-systemctl.rules
rm -f /etc/sudoers.d/scrapper

# Anwendungscode, venv und Units bleiben root-eigen. Nur Laufzeitdaten sind für
# den Dienstbenutzer schreibbar; damit kann ein App-Exploit keinen Code ersetzen.
chown -R root:root "$APP_DIR"
chmod 0755 "$APP_DIR"
chmod -R a+rX "$APP_DIR/playwright-browsers"
chown -R "$APP_USER:$APP_USER" \
  "$APP_DIR/data" "$APP_DIR/logs" "$APP_DIR/temp" "$APP_DIR/files"

systemctl daemon-reload

# 9. Web-Service starten + enablen
systemctl enable --now scrapper-web.service
systemctl enable --now scrapper-job.timer
systemctl enable --now scrapper-db-backup.timer
# 10. Status anzeigen
sleep 2
echo ""
echo "─────────────────────────────────────────────"
systemctl status scrapper-web.service --no-pager -l | head -n 10 || true
echo "─────────────────────────────────────────────"
echo ""

# IP rausfinden
IP=$(hostname -I | awk '{print $1}')

echo "✅ Installation abgeschlossen!"
echo ""
echo "🌐 Web-Interface (LOKAL):    http://127.0.0.1:8000"
echo "                              (im Container - extern via Reverse-Proxy/CF-Tunnel)"
echo "👤 Login:                    admin"
if [[ -n "${INITIAL_PASSWORD:-}" ]]; then
  echo "🔑 Initial-Passwort:         $INITIAL_PASSWORD"
  echo "                              (auch in $APP_DIR/data/.initial-password)"
else
  echo "🔑 Passwort:                 siehe data/config.yaml oder via:"
  echo "                              sudo -u $APP_USER $APP_DIR/venv/bin/python -m app.cli set-password"
fi
echo ""
echo "📁 App-Verzeichnis:  $APP_DIR"
echo "📝 Config:           $APP_DIR/data/config.yaml"
echo "📋 Logs:             $APP_DIR/logs/"
echo ""
echo "Erste Schritte:"
echo "  1. Reverse-Proxy oder Cloudflare-Tunnel davorstellen (uvicorn lauscht nur auf 127.0.0.1)"
echo "  2. Web-UI öffnen, auf 'Einstellungen' gehen, Passwort ändern"
echo "  3. E-Mail-Konten + OpenAI API-Key eintragen"
echo "  4. Erster Test-Lauf via Web-UI"
echo ""
echo "Service-Befehle:"
echo "  systemctl status scrapper-web"
echo "  systemctl restart scrapper-web"
echo "  journalctl -u scrapper-web -f"
echo ""
echo "Passwort zurücksetzen:"
echo "  sudo -u $APP_USER $APP_DIR/venv/bin/python -m app.cli set-password"
