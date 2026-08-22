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

# 4. Repository klonen
if [[ ! -d "$APP_DIR" ]]; then
  echo "📥 Klone Repository..."
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
else
  echo "🔄 Repository aktualisieren..."
  cd "$APP_DIR"
  git pull
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
chown -R $APP_USER:$APP_USER "$APP_DIR"

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
cp "$APP_DIR/systemd/scrapper-web.service" /etc/systemd/system/
cp "$APP_DIR/systemd/scrapper-job.service" /etc/systemd/system/
cp "$APP_DIR/systemd/scrapper-job.timer"   /etc/systemd/system/
cp "$APP_DIR/systemd/scrapper-db-backup.service" /etc/systemd/system/
cp "$APP_DIR/systemd/scrapper-db-backup.timer"   /etc/systemd/system/

# Polkit erlaubt nur daemon-reload und die Verwaltung von scrapper-job.timer.
# Die Timerdatei selbst bleibt die einzige unter /etc für den Webdienst
# beschreibbare Datei (siehe ReadWritePaths in scrapper-web.service).
install -m 0644 "$APP_DIR/systemd/49-scrapper-systemctl.rules" \
  /etc/polkit-1/rules.d/49-scrapper-systemctl.rules
rm -f /etc/sudoers.d/scrapper
chgrp $APP_USER /etc/systemd/system/scrapper-job.timer
chmod 0664 /etc/systemd/system/scrapper-job.timer

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
echo "  3. E-Mail-Konten + KI-Provider (OpenAI/Ollama) eintragen"
echo "  4. Erster Test-Lauf via Web-UI"
echo ""
echo "Service-Befehle:"
echo "  systemctl status scrapper-web"
echo "  systemctl restart scrapper-web"
echo "  journalctl -u scrapper-web -f"
echo ""
echo "Passwort zurücksetzen:"
echo "  sudo -u $APP_USER $APP_DIR/venv/bin/python -m app.cli set-password"
