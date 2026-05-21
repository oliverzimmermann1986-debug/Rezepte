#!/usr/bin/env bash
# ============================================================
# In-Container Installation
# Führe das innerhalb des LXC Containers aus.
# ============================================================
set -euo pipefail

APP_DIR="/opt/scrapper"
APP_USER="scrapper"
REPO_URL="${REPO_URL:-https://github.com/appear7240/Scrappercontainer.git}"
BRANCH="${BRANCH:-main}"

echo "▶️  Scrapper Installation startet..."

# 1. System aktualisieren
echo "📦 System Update..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y upgrade
apt-get install -y --no-install-recommends \
  ca-certificates curl wget gnupg git \
  python3 python3-venv python3-pip python3-dev \
  build-essential ffmpeg \
  rclone \
  sqlite3 \
  cron \
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

# 6. yt-dlp via pip (immer aktuelle Version)
"$APP_DIR/venv/bin/pip" install -U yt-dlp

# 7. Verzeichnisse anlegen
mkdir -p "$APP_DIR/data" "$APP_DIR/logs" "$APP_DIR/temp"
mkdir -p /mnt/rezepte /mnt/hochzeit
chown -R $APP_USER:$APP_USER "$APP_DIR" /mnt/rezepte /mnt/hochzeit

# 8. Default-Config erstellen wenn fehlt
if [[ ! -f "$APP_DIR/data/config.yaml" ]]; then
  echo "📝 Erstelle Default-Config..."
  cp "$APP_DIR/config/config.example.yaml" "$APP_DIR/data/config.yaml"
  chown $APP_USER:$APP_USER "$APP_DIR/data/config.yaml"
  chmod 600 "$APP_DIR/data/config.yaml"
fi

# 9. systemd Services installieren
echo "⚙️  Installiere systemd Units..."
cp "$APP_DIR/systemd/scrapper-web.service" /etc/systemd/system/
cp "$APP_DIR/systemd/scrapper-job.service" /etc/systemd/system/
cp "$APP_DIR/systemd/scrapper-job.timer"   /etc/systemd/system/
cp "$APP_DIR/systemd/rclone-sync.service"  /etc/systemd/system/
cp "$APP_DIR/systemd/rclone-sync.timer"    /etc/systemd/system/

# sudoers-Eintrag damit scrapper Timer-Files schreiben + systemd neuladen darf
install -m 0440 "$APP_DIR/systemd/sudoers-scrapper" /etc/sudoers.d/scrapper
chgrp $APP_USER /etc/systemd/system/scrapper-job.timer /etc/systemd/system/rclone-sync.timer
chmod 0664 /etc/systemd/system/scrapper-job.timer /etc/systemd/system/rclone-sync.timer

systemctl daemon-reload

# 10. Web-Service starten + enablen
systemctl enable --now scrapper-web.service
systemctl enable --now scrapper-job.timer
systemctl enable --now rclone-sync.timer

# 11. Status anzeigen
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
echo "🌐 Web-Interface:    http://$IP:8000"
echo "👤 Login:            admin / changeme  (in config.yaml ändern!)"
echo ""
echo "📁 App-Verzeichnis:  $APP_DIR"
echo "📝 Config:           $APP_DIR/data/config.yaml"
echo "📋 Logs:             $APP_DIR/logs/"
echo ""
echo "Erste Schritte:"
echo "  1. Web-UI öffnen, auf 'Einstellungen' gehen"
echo "  2. E-Mail-Konten, Telegram, Ollama eintragen"
echo "  3. rclone konfigurieren:   sudo -u $APP_USER rclone config"
echo "  4. Erster Test-Lauf via Web-UI"
echo ""
echo "Service-Befehle:"
echo "  systemctl status scrapper-web"
echo "  systemctl restart scrapper-web"
echo "  journalctl -u scrapper-web -f"
