#!/usr/bin/env bash
# Installiert dieses lokal entpackte Release vollständig nach /opt/scrapper.
# Im Gegensatz zu install.sh wird KEIN git pull ausgeführt. Dadurch können
# Frontend und bereits laufendes Backend nicht versehentlich auf verschiedenen
# Versionsständen bleiben.
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/scrapper}"
APP_USER="${APP_USER:-scrapper}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_ROOT="${BACKUP_ROOT:-/opt/scrapper-code-backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_FILE="$BACKUP_ROOT/code-$STAMP.tar.gz"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Fehler: Bitte als root ausführen." >&2
  exit 1
fi
if [[ ! -f "$SOURCE_DIR/app/main.py" || ! -f "$SOURCE_DIR/requirements.txt" ]]; then
  echo "Fehler: Das Skript muss aus einem vollständig entpackten Rezepte-Release stammen." >&2
  exit 1
fi
if ! id "$APP_USER" >/dev/null 2>&1; then
  echo "Fehler: Benutzer $APP_USER existiert nicht. Für eine Erstinstallation proxmox/install.sh verwenden." >&2
  exit 1
fi

if [[ "$SOURCE_DIR" != "$APP_DIR" && "$SOURCE_DIR" == "$APP_DIR/"* ]]; then
  echo "Fehler: Das entpackte Release darf nicht innerhalb von $APP_DIR liegen." >&2
  exit 1
fi

OCR_PACKAGES=(tesseract-ocr tesseract-ocr-osd tesseract-ocr-deu tesseract-ocr-eng)
MISSING_PACKAGES=()
for pkg in "${OCR_PACKAGES[@]}"; do
  dpkg -s "$pkg" >/dev/null 2>&1 || MISSING_PACKAGES+=("$pkg")
done
if (( ${#MISSING_PACKAGES[@]} )); then
  apt-get update
  apt-get install -y --no-install-recommends "${MISSING_PACKAGES[@]}"
fi
if [[ "$SOURCE_DIR" != "$APP_DIR" ]] && ! command -v rsync >/dev/null 2>&1; then
  apt-get update
  apt-get install -y --no-install-recommends rsync
fi

mkdir -p "$BACKUP_ROOT"
if [[ -d "$APP_DIR/app" ]]; then
  echo "Sichere bisherigen Anwendungscode nach $BACKUP_FILE"
  tar -C "$APP_DIR" -czf "$BACKUP_FILE" \
    --exclude='./data' --exclude='./venv' --exclude='./logs' \
    --exclude='./temp' --exclude='./files' --exclude='./playwright-browsers' \
    --exclude='./.git' .
fi

systemctl stop scrapper-job.timer scrapper-db-backup.timer 2>/dev/null || true
systemctl stop scrapper-job.service scrapper-db-backup.service 2>/dev/null || true
systemctl stop scrapper-web.service 2>/dev/null || true

VENV_SWAPPED=0
BROWSERS_SWAPPED=0

restore_on_error() {
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "Update fehlgeschlagen (Code $rc). Stelle bisherigen Code wieder her…" >&2
    if [[ -f "$BACKUP_FILE" ]]; then
      RESTORE_DIR="$(mktemp -d /tmp/rezepte-restore.XXXXXX)"
      tar -C "$RESTORE_DIR" -xzf "$BACKUP_FILE"
      rsync -a --delete \
        --exclude='/data/' --exclude='/venv/' --exclude='/logs/' \
        --exclude='/temp/' --exclude='/files/' --exclude='/playwright-browsers/' \
        --exclude='/.git/' \
        "$RESTORE_DIR/" "$APP_DIR/"
      rm -rf -- "$RESTORE_DIR"
      if [[ "$VENV_SWAPPED" == "1" && -d "$APP_DIR/venv.previous" ]]; then
        rm -rf -- "$APP_DIR/venv"
        mv "$APP_DIR/venv.previous" "$APP_DIR/venv"
      fi
      if [[ "$BROWSERS_SWAPPED" == "1" && -d "$APP_DIR/playwright-browsers.previous" ]]; then
        rm -rf -- "$APP_DIR/playwright-browsers"
        mv "$APP_DIR/playwright-browsers.previous" "$APP_DIR/playwright-browsers"
      fi
      systemctl daemon-reload || true
      systemctl restart scrapper-web.service || true
      systemctl restart scrapper-job.timer scrapper-db-backup.timer || true
    fi
  fi
  exit "$rc"
}
trap restore_on_error ERR

mkdir -p "$APP_DIR"
if [[ "$SOURCE_DIR" != "$APP_DIR" ]]; then
  echo "Übertrage Anwendungscode vollständig…"
  rsync -a --delete \
    --exclude='/data/' --exclude='/venv/' --exclude='/logs/' \
    --exclude='/temp/' --exclude='/files/' --exclude='/playwright-browsers/' \
    --exclude='/.git/' \
    "$SOURCE_DIR/" "$APP_DIR/"
else
  echo "Release liegt bereits in $APP_DIR; aktualisiere Abhängigkeiten und Dienste."
fi

# `mktemp -d` erzeugt Release-Verzeichnisse mit 0700. `rsync -a` übernimmt
# diesen Modus sonst auf APP_DIR und systemd kann als APP_USER nicht hinein.
chmod 0755 "$APP_DIR"

rm -rf -- "$APP_DIR/venv.next" "$APP_DIR/playwright-browsers.next"
python3 -m venv "$APP_DIR/venv.next" --upgrade-deps
"$APP_DIR/venv.next/bin/pip" install -r "$APP_DIR/requirements.txt"
PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/playwright-browsers.next" \
  "$APP_DIR/venv.next/bin/python" -m playwright install --with-deps chromium
chmod -R a+rX "$APP_DIR/playwright-browsers.next"
rm -rf -- "$APP_DIR/venv.previous" "$APP_DIR/playwright-browsers.previous"
mv "$APP_DIR/venv" "$APP_DIR/venv.previous"
mv "$APP_DIR/venv.next" "$APP_DIR/venv"
VENV_SWAPPED=1
# pip erzeugt Konsolenskripte mit einem absoluten Shebang auf den Build-Pfad
# ``venv.next``. Nach dem atomaren Rename bliebe z. B. ``yt-dlp`` trotz
# vorhandener Datei nicht startbar. Der Alias hält diese Entry-Points gültig;
# beim nächsten Update wird er vor dem Neubau kontrolliert entfernt.
ln -s "$APP_DIR/venv" "$APP_DIR/venv.next"
if ! "$APP_DIR/venv/bin/yt-dlp" --version >/tmp/rezepte-ytdlp-version.txt; then
  echo "Fehler: yt-dlp ist nach dem Venv-Tausch nicht ausführbar." >&2
  false
fi
if ! "$APP_DIR/venv/bin/yt-dlp" --list-impersonate-targets \
  | grep -E 'curl_cffi' \
  | grep -vq 'unavailable'; then
  echo "Fehler: yt-dlp hat kein verfügbares curl_cffi-Impersonation-Ziel für TikTok." >&2
  false
fi
if [[ -d "$APP_DIR/playwright-browsers" ]]; then
  mv "$APP_DIR/playwright-browsers" "$APP_DIR/playwright-browsers.previous"
fi
mv "$APP_DIR/playwright-browsers.next" "$APP_DIR/playwright-browsers"
BROWSERS_SWAPPED=1

install -m 0644 "$APP_DIR/systemd/scrapper-web.service" /etc/systemd/system/scrapper-web.service
install -m 0644 "$APP_DIR/systemd/scrapper-job.service" /etc/systemd/system/scrapper-job.service
install -m 0644 "$APP_DIR/systemd/scrapper-job.timer" /etc/systemd/system/scrapper-job.timer
install -m 0644 "$APP_DIR/systemd/scrapper-db-backup.service" /etc/systemd/system/scrapper-db-backup.service
install -m 0644 "$APP_DIR/systemd/scrapper-db-backup.timer" /etc/systemd/system/scrapper-db-backup.timer
install -m 0644 "$APP_DIR/systemd/scrapper-schedule-apply.service" \
  /etc/systemd/system/scrapper-schedule-apply.service
install -d -m 0755 /etc/systemd/system/scrapper-job.timer.d
install -d -m 0755 /etc/scrapper
if [[ -f "$APP_DIR/data/web.env" && ! -f /etc/scrapper/web.env ]]; then
  install -m 0600 -o root -g root "$APP_DIR/data/web.env" /etc/scrapper/web.env
fi
install -m 0644 "$APP_DIR/systemd/49-scrapper-systemctl.rules" \
  /etc/polkit-1/rules.d/49-scrapper-systemctl.rules
rm -f /etc/sudoers.d/scrapper

mkdir -p "$APP_DIR/data" "$APP_DIR/logs" "$APP_DIR/temp" "$APP_DIR/files/rezepte"
chown -R "$APP_USER:$APP_USER" "$APP_DIR/data" "$APP_DIR/logs" "$APP_DIR/temp" "$APP_DIR/files"
find "$APP_DIR" -path "$APP_DIR/data" -prune -o -path "$APP_DIR/logs" -prune \
  -o -path "$APP_DIR/temp" -prune -o -path "$APP_DIR/files" -prune \
  -o -exec chown root:root {} +

systemctl daemon-reload
systemctl enable scrapper-web.service scrapper-job.timer scrapper-db-backup.timer >/dev/null
systemctl restart scrapper-web.service
systemctl restart scrapper-job.timer
systemctl restart scrapper-db-backup.timer

for _ in {1..30}; do
  if curl -fsS http://127.0.0.1:8000/healthz > /tmp/rezepte-health.json 2>/dev/null; then
    break
  fi
  sleep 1
done

EXPECTED_VERSION="$(sed -n 's/^APP_VERSION = "\([^"]*\)"/\1/p' "$APP_DIR/app/main.py" | head -n 1 | tr -d '\r')"
HEALTH_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version", ""))' /tmp/rezepte-health.json 2>/dev/null || true)"
if [[ -z "$EXPECTED_VERSION" || "$HEALTH_VERSION" != "$EXPECTED_VERSION" ]]; then
  echo "Fehler: Erwartete Version '$EXPECTED_VERSION', Dienst meldet '$HEALTH_VERSION'." >&2
  journalctl -u scrapper-web.service -n 80 --no-pager >&2 || true
  false
fi

for REQUIRED_CAPABILITY in ai-shopping-optimization shopping-categories native-admin-roles; do
  if ! python3 -c 'import json,sys; raise SystemExit(0 if sys.argv[2] in json.load(open(sys.argv[1])).get("capabilities", []) else 1)' \
      /tmp/rezepte-health.json "$REQUIRED_CAPABILITY"; then
    echo "Fehler: Backend-Faehigkeit '$REQUIRED_CAPABILITY' fehlt nach dem Update." >&2
    journalctl -u scrapper-web.service -n 80 --no-pager >&2 || true
    false
  fi
done

# Die native App braucht diese Route fuer KI-Sortierung und Kategorien. Ohne
# Cookie ist 401 korrekt; 404 oder keine Verbindung beweist einen alten Server.
OPTIMIZER_STATUS="$(curl -sS -o /tmp/rezepte-optimizer-route.json -w '%{http_code}' \
  -X POST http://127.0.0.1:8000/api/cart/optimize/preview || true)"
if [[ "$OPTIMIZER_STATUS" == "404" || "$OPTIMIZER_STATUS" == "000" ]]; then
  echo "Fehler: Einkaufslisten-KI wurde nach dem Update nicht registriert (HTTP $OPTIMIZER_STATUS)." >&2
  journalctl -u scrapper-web.service -n 80 --no-pager >&2 || true
  false
fi

# PDF-Route muss nach dem Neustart existieren. Ohne Cookie ist 401 korrekt;
# nur 404 würde erneut einen gemischten Versionsstand beweisen.
PDF_STATUS="$(curl -sS -o /tmp/rezepte-pdf-route.json -w '%{http_code}' \
  http://127.0.0.1:8000/api/admin/pdf/preflight || true)"
if [[ "$PDF_STATUS" == "404" || "$PDF_STATUS" == "000" ]]; then
  echo "Fehler: PDF-API wurde nach dem Update nicht registriert (HTTP $PDF_STATUS)." >&2
  journalctl -u scrapper-web.service -n 80 --no-pager >&2 || true
  false
fi

trap - ERR
rm -rf -- "$APP_DIR/venv.previous" "$APP_DIR/playwright-browsers.previous"
echo "Update erfolgreich. Backend und Frontend laufen gemeinsam auf Version $EXPECTED_VERSION."
echo "Gesundheit: $(cat /tmp/rezepte-health.json)"
echo "Einkaufslisten-KI: HTTP $OPTIMIZER_STATUS (400, 401 oder 422 sind ohne Nutzdaten korrekt)"
echo "PDF-Route: HTTP $PDF_STATUS (200 oder 401 sind korrekt)"
