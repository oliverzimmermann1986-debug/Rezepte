#!/usr/bin/env bash
# ============================================================
# In-Container Installation
# Führe das innerhalb des LXC Containers aus.
# ============================================================
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "❌ Dieses Script muss als root im LXC ausgeführt werden." >&2
  exit 1
fi

APP_DIR="/opt/scrapper"
APP_USER="scrapper"
REPO_URL="${REPO_URL:-https://github.com/appear7240/Scrappercontainer.git}"
BRANCH="${BRANCH:-main}"
UPDATE_SERVICES_STOPPED=0

[[ -n "$REPO_URL" && "$REPO_URL" != -* && "$REPO_URL" != *$'\n'* && "$REPO_URL" != *$'\r'* ]] \
  || { echo "❌ Ungültige REPO_URL" >&2; exit 1; }
[[ "$BRANCH" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$ \
   && "$BRANCH" != *..* && "$BRANCH" != *//* && "$BRANCH" != *@\{* ]] \
  || { echo "❌ Ungültiger BRANCH" >&2; exit 1; }

normalize_mnt_subdir() {
  local raw="$1" normalized
  [[ -n "$raw" && "$raw" != *$'\n'* && "$raw" != *$'\r'* && "$raw" != *','* ]] || return 1
  normalized="$(readlink -m -- "$raw")" || return 1
  [[ "$normalized" == /mnt/* && "$normalized" != /mnt ]] || return 1
  printf '%s\n' "$normalized"
}

restore_services_on_error() {
  local rc=$?
  trap - EXIT
  if [[ $rc -ne 0 && $UPDATE_SERVICES_STOPPED -eq 1 ]]; then
    echo "⚠️  Installation fehlgeschlagen - versuche bisherige Dienste wieder zu starten..." >&2
    systemctl daemon-reload 2>/dev/null || true
    systemctl start scrapper-web.service scrapper-job.timer \
      scrapper-db-backup.timer \
      scrapper-schedule-apply.path scrapper-hdd-action.path 2>/dev/null || true
  fi
  exit "$rc"
}
trap restore_services_on_error EXIT

echo "▶️  Scrapper Installation startet..."

# 1. System aktualisieren
echo "📦 System Update..."
export DEBIAN_FRONTEND=noninteractive
apt-get update
# Ein vollständiges Distribution-Upgrade ist bei einem App-Update riskant und
# deshalb opt-in: SYSTEM_UPGRADE=1 bash proxmox/install.sh
if [[ "${SYSTEM_UPGRADE:-0}" == "1" ]]; then
  apt-get -y upgrade
fi
apt-get install -y --no-install-recommends \
  ca-certificates curl git \
  python3 python3-venv python3-pip python3-dev \
  build-essential ffmpeg \
  sqlite3 \
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
  git clone --branch "$BRANCH" -- "$REPO_URL" "$APP_DIR"
else
  echo "🔄 Repository aktualisieren..."
  # Timer und Webdienst zuerst stoppen, damit während Code-/Dependency-Updates
  # kein Job mit halb aktualisierten Modulen startet.
  systemctl stop scrapper-job.timer \
    scrapper-db-backup.timer scrapper-schedule-apply.path \
    scrapper-hdd-action.path scrapper-web.service 2>/dev/null || true
  systemctl stop scrapper-job.service 2>/dev/null || true
  UPDATE_SERVICES_STOPPED=1

  # Vor jedem Update ein konsistentes SQLite-Sicherungsabbild ablegen.
  if [[ -f "$APP_DIR/data/scrapper.db" ]]; then
    PREUPDATE_DIR="$APP_DIR/data/backups/pre-update"
    mkdir -p "$PREUPDATE_DIR"
    PREUPDATE_DB="$PREUPDATE_DIR/scrapper-$(date +%Y%m%d-%H%M%S).db"
    sqlite3 "$APP_DIR/data/scrapper.db" ".timeout 10000" ".backup '$PREUPDATE_DB'"
    gzip -f "$PREUPDATE_DB"
    chown -R "$APP_USER:$APP_USER" "$PREUPDATE_DIR"
    chmod 600 "$PREUPDATE_DB.gz"
    echo "💾 Pre-Update-DB-Backup: $PREUPDATE_DB.gz"
  fi
  cd "$APP_DIR"
  if [[ ! -d .git ]]; then
    echo "❌ $APP_DIR existiert, ist aber kein Git-Repository. Update abgebrochen." >&2
    exit 1
  fi
  git fetch --prune origin "$BRANCH"
  git checkout "$BRANCH"
  git merge --ff-only "origin/$BRANCH"
fi

cd "$APP_DIR"

# 5. Python Virtual Environment
echo "🐍 Python venv..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip wheel
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# 6. Verzeichnisse anlegen. yt-dlp wird reproduzierbar über requirements.txt installiert.

# Default-Ablage für sortierte Videos liegt INNERHALB des Containers
# unter /opt/scrapper/files/. Wenn du z.B. einen Bind-Mount willst,
# editier nach der Installation einfach data/config.yaml -> paths:
# und passe das Verzeichnis an deinen Mount an.
install -d -o "$APP_USER" -g "$APP_USER" -m 0700 \
  "$APP_DIR/data" "$APP_DIR/logs" "$APP_DIR/temp" \
  "$APP_DIR/data/locks"
install -d -o "$APP_USER" -g "$APP_USER" -m 0750 \
  "$APP_DIR/files" "$APP_DIR/files/rezepte" "$APP_DIR/files/hochzeit"

# 7. Default-Config erstellen wenn fehlt
if [[ ! -f "$APP_DIR/data/config.yaml" ]]; then
  echo "📝 Erstelle Default-Config mit zufälligem Passwort + Secret..."
  cp "$APP_DIR/config/config.example.yaml" "$APP_DIR/data/config.yaml"

  GEN_PASS=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
  GEN_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
  GEN_METRICS_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')

  # Default-Platzhalter durch zufällige Werte ersetzen (sed -i, sauber escaped)
  sed -i "s|password: changeme|password: ${GEN_PASS}|" "$APP_DIR/data/config.yaml"
  sed -i "s|secret_key: change-this-to-random-string-32chars-min|secret_key: ${GEN_SECRET}|" "$APP_DIR/data/config.yaml"
  sed -i "s|metrics_token: change-this-metrics-token|metrics_token: ${GEN_METRICS_TOKEN}|" "$APP_DIR/data/config.yaml"

  chown $APP_USER:$APP_USER "$APP_DIR/data/config.yaml"
  chmod 600 "$APP_DIR/data/config.yaml"

  # Generiertes Passwort separat ablegen, damit es nicht im Terminal-Scrollback hängt
  echo "$GEN_PASS" > "$APP_DIR/data/.initial-password"
  chown $APP_USER:$APP_USER "$APP_DIR/data/.initial-password"
  chmod 600 "$APP_DIR/data/.initial-password"

  INITIAL_PASSWORD="$GEN_PASS"
fi


# Alte Dateisynchronisierungs-Konfiguration aus früheren Versionen entfernen.
# Vorherige Config bleibt als .bak erhalten.
"$APP_DIR/venv/bin/python" - <<'PYCFG'
from pathlib import Path
import shutil, yaml
p = Path("/opt/scrapper/data/config.yaml")
if p.is_file():
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if isinstance(data, dict) and "backup" in data:
        shutil.copy2(p, p.with_name(p.name + ".bak"))
        data.pop("backup", None)
        p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        p.chmod(0o600)
PYCFG
rm -rf "$APP_DIR/data/.rclone-cache" "$APP_DIR/data/rclone-filters.txt"

# Code, venv und Git-Metadaten bleiben root-owned. Der Dienst darf ausschließlich
# die Runtime-Verzeichnisse verändern; ein kompromittierter Webprozess kann so
# weder Python-Code noch seine nächste Startversion persistent manipulieren.
for protected in app config proxmox systemd tests venv .git \
  requirements.txt requirements-dev.txt pytest.ini README.md AUDIT_FIXES.md \
  PUSH_INSTRUCTIONS.txt .gitignore; do
  [[ -e "$APP_DIR/$protected" ]] && chown -R root:root "$APP_DIR/$protected"
done
chmod -R go-w "$APP_DIR/app" "$APP_DIR/config" "$APP_DIR/proxmox" \
  "$APP_DIR/systemd" "$APP_DIR/venv" 2>/dev/null || true
chown -R "$APP_USER:$APP_USER" "$APP_DIR/data" "$APP_DIR/logs" "$APP_DIR/temp"
chown "$APP_USER:$APP_USER" "$APP_DIR/files" "$APP_DIR/files/rezepte" "$APP_DIR/files/hochzeit"
"$APP_DIR/venv/bin/python" -m compileall -q "$APP_DIR/app"

# 8. systemd Services installieren
echo "⚙️  Installiere systemd Units..."
for unit in   scrapper-web.service   scrapper-job.service scrapper-job.timer   scrapper-db-backup.service scrapper-db-backup.timer \
  scrapper-schedule-apply.service scrapper-schedule-apply.path \
  scrapper-hdd-action.service scrapper-hdd-action.path; do
  install -m 0644 "$APP_DIR/systemd/$unit" "/etc/systemd/system/$unit"
done

# Nur der Scraper-OnCalendar-Timer wird über das Web bearbeitet.
install -o root -g root -m 0755 "$APP_DIR/systemd/scrapper-set-schedule" /usr/local/sbin/scrapper-set-schedule
install -o root -g root -m 0755 "$APP_DIR/systemd/scrapper-hdd-action" /usr/local/sbin/scrapper-hdd-action
# Alte sudo-basierte Version entfernen: Zeitplanänderungen laufen jetzt über eine
# root-eigene systemd Path-Unit, der Webdienst behält NoNewPrivileges=true.
rm -f /etc/sudoers.d/scrapper
rm -f /etc/sudoers.d/scrapper-hdd
chown root:root /etc/systemd/system/scrapper-job.timer
chmod 0644 /etc/systemd/system/scrapper-job.timer

# Root-seitige Allowlist für genau einen HDD-Mountpunkt. Eine spätere Änderung
# erfolgt bewusst nicht über das Web-UI, sondern explizit über diese Datei oder
# SCRAPPER_HDD_MOUNT_POINT beim Installer-Aufruf.
if [[ -z "${SCRAPPER_HDD_MOUNT_POINT:-}" && -f /etc/scrapper-hdd-mountpoint ]]; then
  HDD_MOUNT_POINT_RAW="$(cat /etc/scrapper-hdd-mountpoint)"
else
  HDD_MOUNT_POINT_RAW="${SCRAPPER_HDD_MOUNT_POINT:-/mnt/external_hdd}"
fi
if ! HDD_MOUNT_POINT="$(normalize_mnt_subdir "$HDD_MOUNT_POINT_RAW")"; then
  echo "❌ HDD-Mount-Allowlist muss ein sicherer Unterordner von /mnt sein" >&2
  exit 1
fi
printf '%s\n' "$HDD_MOUNT_POINT" > /etc/scrapper-hdd-mountpoint
chown root:root /etc/scrapper-hdd-mountpoint
chmod 0644 /etc/scrapper-hdd-mountpoint
install -d -o root -g root -m 0755 -- "$HDD_MOUNT_POINT"


# Nicht mehr verwendete Units früherer Versionen vollständig deaktivieren.
systemctl disable --now rclone-sync.timer rclone-sync.service \
  scrapper-scheduler.timer scrapper-scheduler.service 2>/dev/null || true
rm -f /etc/systemd/system/rclone-sync.timer /etc/systemd/system/rclone-sync.service \
  /etc/systemd/system/scrapper-scheduler.timer /etc/systemd/system/scrapper-scheduler.service

systemctl daemon-reload

# 9. Dienste und Timer aktivieren.
systemctl enable --now scrapper-web.service
systemctl enable --now scrapper-job.timer
systemctl enable --now scrapper-db-backup.timer
systemctl enable --now scrapper-schedule-apply.path
systemctl enable --now scrapper-hdd-action.path

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
  echo "🔑 Initial-Passwort:         sicher gespeichert in $APP_DIR/data/.initial-password"
  echo "                              anzeigen: cat $APP_DIR/data/.initial-password"
else
  echo "🔑 Passwort:                 siehe data/config.yaml oder via:"
  echo "                              runuser -u $APP_USER -- $APP_DIR/venv/bin/python -m app.cli set-password"
fi
echo ""
echo "📁 App-Verzeichnis:  $APP_DIR"
echo "📝 Config:           $APP_DIR/data/config.yaml"
echo "📋 Logs:             $APP_DIR/logs/"
echo ""
echo "Erste Schritte:"
echo "  1. Reverse-Proxy oder Cloudflare-Tunnel davorstellen (uvicorn lauscht nur auf 127.0.0.1)"
echo "  2. Web-UI öffnen, auf 'Einstellungen' gehen, Passwort ändern"
echo "  3. E-Mail-Konten und KI-Provider eintragen"
echo "  4. Erster Test-Import über die Web-Oberfläche"
echo "  5. Optional HDD-Control: /etc/fstab + /etc/scrapper-hdd-mountpoint prüfen"
echo ""
echo "Service-Befehle:"
echo "  systemctl status scrapper-web"
echo "  systemctl restart scrapper-web"
echo "  systemctl list-timers 'scrapper-*'"
echo "  journalctl -u scrapper-web -f"
echo ""
echo "Passwort zurücksetzen:"
echo "  runuser -u $APP_USER -- $APP_DIR/venv/bin/python -m app.cli set-password"

UPDATE_SERVICES_STOPPED=0
