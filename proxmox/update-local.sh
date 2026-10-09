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
STAMP="$(date +%Y%m%d-%H%M%S)-$$"
BACKUP_FILE="$BACKUP_ROOT/code-$STAMP.tar.gz"
STATE_DIR="$BACKUP_ROOT/state-$STAMP"
HEALTH_FILE=""
REVIEW_HEALTH_FILE=""
ROLLBACK_HEALTH_FILE=""

cleanup_health_files() {
  [[ -z "$HEALTH_FILE" ]] || rm -f -- "$HEALTH_FILE" || true
  [[ -z "$REVIEW_HEALTH_FILE" ]] || rm -f -- "$REVIEW_HEALTH_FILE" || true
  [[ -z "$ROLLBACK_HEALTH_FILE" ]] || rm -f -- "$ROLLBACK_HEALTH_FILE" || true
}

poll_local_health() {
  local output_file="$1"
  for _ in {1..30}; do
    if curl -fsS --connect-timeout 1 --max-time 2 \
      --output "$output_file" http://127.0.0.1:8000/healthz 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

trap cleanup_health_files EXIT

# Die App-Review-Instanz (review-demo/DEPLOYMENT.md) betreibt bewusst keinen
# Import-Job; ein Update darf scrapper-job.timer dort nicht reaktivieren.
REVIEW_MARKER="/etc/scrapper/review-instance"
IS_REVIEW_INSTANCE=0
if [[ -f "$REVIEW_MARKER" || "$(hostname)" == "rezepte-review" ]]; then
  IS_REVIEW_INSTANCE=1
fi

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Fehler: Bitte als root ausführen." >&2
  exit 1
fi
if [[ "$(hostname)" == "rezepte-review" && ! -f "$REVIEW_MARKER" ]]; then
  # Der Hostname ist die bestehende, dokumentierte Zweit-Erkennung. Fehlt der
  # Marker auf einer älteren Review-Instanz, wird die Isolation dauerhaft
  # selbst repariert, bevor irgendein Timer neu konfiguriert werden kann.
  install -d -m 0755 "$(dirname "$REVIEW_MARKER")"
  install -m 0644 /dev/null "$REVIEW_MARKER"
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

mkdir -p "$BACKUP_ROOT"
chmod 0700 "$BACKUP_ROOT"
exec 9>"$BACKUP_ROOT/update.lock"
if ! flock -n 9; then
  echo "Fehler: Ein anderes Update läuft bereits." >&2
  exit 1
fi

# Keine älteren Schemas oder fehlenden bestehenden Fähigkeiten ausrollen.
# Der Helfer liest SQLite ausschließlich read-only und importiert keine App.
"$APP_DIR/venv/bin/python" "$SOURCE_DIR/tools/release_state.py" preflight \
  --app "$APP_DIR" --source "$SOURCE_DIR"
for writer in scrapper-job.service scrapper-db-backup.service; do
  if systemctl is-active --quiet "$writer"; then
    echo "Fehler: $writer läuft. Update nach Abschluss erneut starten." >&2
    exit 1
  fi
done
declare -A TIMER_WAS_ACTIVE
for timer in scrapper-job.timer scrapper-db-backup.timer; do
  TIMER_WAS_ACTIVE["$timer"]=0
  if systemctl is-active --quiet "$timer"; then
    TIMER_WAS_ACTIVE["$timer"]=1
  fi
done

unit_load_state() {
  local state
  state="$(systemctl show "$1" --property=LoadState --value)" || {
    [[ "$state" == "not-found" ]] || return 1
  }
  [[ -n "$state" ]] || return 1
  printf '%s\n' "$state"
}

stop_installed_units() {
  local unit state
  for unit in "$@"; do
    state="$(unit_load_state "$unit")" || return 1
    [[ "$state" == "not-found" ]] && continue
    systemctl stop "$unit" || return 1
  done
}

restore_timer_activity() {
  local retire_mail="${1:-0}"
  local timer state
  for timer in scrapper-job.timer scrapper-db-backup.timer; do
    if [[ "$timer" == "scrapper-job.timer" ]]; then
      state="$(unit_load_state "$timer")" || return 1
      [[ "$state" == "not-found" ]] && continue
    fi
    if [[ "$timer" == "scrapper-job.timer" && ( "$retire_mail" == "1" || "$IS_REVIEW_INSTANCE" == "1" ) ]]; then
      systemctl disable --now "$timer" >/dev/null
    elif [[ "${TIMER_WAS_ACTIVE[$timer]}" == "1" ]]; then
      systemctl start "$timer"
    else
      systemctl stop "$timer"
    fi
  done
}

OCR_PACKAGES=(poppler-utils tesseract-ocr tesseract-ocr-osd tesseract-ocr-deu tesseract-ocr-eng)
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

if [[ -d "$APP_DIR/app" ]]; then
  echo "Sichere bisherigen Anwendungscode nach $BACKUP_FILE"
  tar -C "$APP_DIR" -czf "$BACKUP_FILE" \
    --exclude='./data' --exclude='./venv' --exclude='./logs' \
    --exclude='./temp' --exclude='./files' --exclude='./playwright-browsers*' \
    --exclude='./venv.*' \
    --exclude='./.git' .
fi

systemctl stop scrapper-job.timer scrapper-db-backup.timer 2>/dev/null || true
systemctl stop scrapper-job.service scrapper-db-backup.service 2>/dev/null || true
systemctl stop scrapper-web.service 2>/dev/null || true

VENV_SWAPPED=0
BROWSERS_SWAPPED=0

restore_on_error() {
  local rc=$?
  local restored=1
  trap - ERR
  if [[ $rc -ne 0 ]]; then
    echo "Update fehlgeschlagen (Code $rc). Stelle bisherigen Code wieder her…" >&2
    if [[ -f "$BACKUP_FILE" ]]; then
      stop_installed_units scrapper-web.service scrapper-job.timer scrapper-db-backup.timer || restored=0
      stop_installed_units scrapper-job.service scrapper-db-backup.service || restored=0
      RESTORE_DIR="$(mktemp -d /tmp/rezepte-restore.XXXXXX)"
      tar -C "$RESTORE_DIR" -xzf "$BACKUP_FILE"
      rsync -a --checksum --delete \
        --exclude='/data/' --exclude='/venv/' --exclude='/logs/' \
        --exclude='/temp/' --exclude='/files/' --exclude='/playwright-browsers/' \
        --exclude='/venv.previous/' --exclude='/venv.next' \
        --exclude='/playwright-browsers.previous/' --exclude='/playwright-browsers.next/' \
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
      if [[ -f "$STATE_DIR/manifest.json" && "$restored" == "1" ]]; then
        "$APP_DIR/venv/bin/python" "$SOURCE_DIR/tools/release_state.py" restore \
          --state "$STATE_DIR" || restored=0
      fi
      systemctl daemon-reload || true
      ROLLBACK_HEALTH_FILE="$(mktemp /tmp/rezepte-rollback-health.XXXXXX)"
      chmod 0600 "$ROLLBACK_HEALTH_FILE"
      if [[ "$restored" == "1" ]] && systemctl restart scrapper-web.service \
          && poll_local_health "$ROLLBACK_HEALTH_FILE"; then
        restore_timer_activity || true
      else
        echo "Wiederherstellung braucht Prüfung. Dienste bleiben angehalten; Sicherung: $STATE_DIR" >&2
        systemctl stop scrapper-web.service || true
      fi
    fi
  fi
  exit "$rc"
}
trap restore_on_error ERR

# Sichert DB, Konfiguration und installierte Units vor dem ersten neuen Code.
# Erst nach angehaltenen Schreibern ist ein Daten-Rollback verlustfrei möglich.
"$APP_DIR/venv/bin/python" "$SOURCE_DIR/tools/release_state.py" capture \
  --app "$APP_DIR" --state "$STATE_DIR"

mkdir -p "$APP_DIR"
if [[ "$SOURCE_DIR" != "$APP_DIR" ]]; then
  echo "Übertrage Anwendungscode vollständig…"
  rsync -a --checksum --delete \
    --exclude='/data/' --exclude='/venv/' --exclude='/logs/' \
    --exclude='/temp/' --exclude='/files/' --exclude='/playwright-browsers/' \
    --exclude='/.git/' \
    "$SOURCE_DIR/" "$APP_DIR/"
else
  echo "Release liegt bereits in $APP_DIR; aktualisiere Abhängigkeiten und Dienste."
fi

"$APP_DIR/venv/bin/python" "$SOURCE_DIR/tools/release_state.py" verify-code \
  --app "$APP_DIR" --source "$SOURCE_DIR"

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

# Deterministic archives can retain identical timestamps and file sizes. Force
# hash-checked bytecode so an old timestamp-based .pyc cannot survive an update.
"$APP_DIR/venv/bin/python" -m compileall -q -f --invalidation-mode checked-hash "$APP_DIR/app"

install -m 0644 "$APP_DIR/systemd/scrapper-web.service" /etc/systemd/system/scrapper-web.service
install -m 0644 "$APP_DIR/systemd/scrapper-db-backup.service" /etc/systemd/system/scrapper-db-backup.service
install -m 0644 "$APP_DIR/systemd/scrapper-db-backup.timer" /etc/systemd/system/scrapper-db-backup.timer
install -d -m 0755 /etc/scrapper
if [[ -f "$APP_DIR/data/web.env" && ! -f /etc/scrapper/web.env ]]; then
  install -m 0600 -o root -g root "$APP_DIR/data/web.env" /etc/scrapper/web.env
fi
rm -f /etc/sudoers.d/scrapper

mkdir -p "$APP_DIR/data" "$APP_DIR/logs" "$APP_DIR/temp" "$APP_DIR/files/rezepte"
chown -R "$APP_USER:$APP_USER" "$APP_DIR/data" "$APP_DIR/logs" "$APP_DIR/temp" "$APP_DIR/files"
find "$APP_DIR" -path "$APP_DIR/data" -prune -o -path "$APP_DIR/logs" -prune \
  -o -path "$APP_DIR/temp" -prune -o -path "$APP_DIR/files" -prune \
  -o -exec chown root:root {} +

systemctl daemon-reload
if [[ "${ENABLE_HOUSEHOLD_AUTH:-0}" == "1" ]]; then
  # Die ursprüngliche Konfiguration ist bereits im Rollback-Snapshot.
  # Bestehende Konten und Kennwörter werden nicht verändert.
  "$APP_DIR/venv/bin/python" "$SOURCE_DIR/tools/release_state.py" enable-accounts --app "$APP_DIR"
fi
if [[ "$IS_REVIEW_INSTANCE" == "1" ]]; then
  systemctl disable --now scrapper-job.timer >/dev/null 2>&1 || true
fi
systemctl restart scrapper-web.service

HEALTH_FILE="$(mktemp /tmp/rezepte-health.XXXXXX)"
chmod 0600 "$HEALTH_FILE"
if ! poll_local_health "$HEALTH_FILE"; then
  echo "Fehler: Der Dienst hat innerhalb des Health-Timeouts nicht geantwortet." >&2
  journalctl -u scrapper-web.service -n 80 --no-pager >&2 || true
  false
fi

EXPECTED_VERSION="$(sed -n 's/^__version__ = "\([^"]*\)"/\1/p' "$SOURCE_DIR/app/__init__.py" | head -n 1 | tr -d '\r')"
HEALTH_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version", ""))' "$HEALTH_FILE" 2>/dev/null || true)"
if [[ -z "$EXPECTED_VERSION" || "$HEALTH_VERSION" != "$EXPECTED_VERSION" ]]; then
  echo "Fehler: Erwartete Version '$EXPECTED_VERSION', Dienst meldet '$HEALTH_VERSION'." >&2
  journalctl -u scrapper-web.service -n 80 --no-pager >&2 || true
  false
fi

for REQUIRED_CAPABILITY in \
  ai-shopping-optimization \
  shopping-categories \
  native-admin-roles \
  native-admin-config-v1 \
  household-libraries-v1 \
  household-invitations-v1 \
  global-recipe-references-v1 \
  recurring-shopping \
  meal-conductor-v1 \
  source-integrity-v2 \
  substitution-lab-v1; do
  if ! python3 -c 'import json,sys; raise SystemExit(0 if sys.argv[2] in json.load(open(sys.argv[1])).get("capabilities", []) else 1)' \
      "$HEALTH_FILE" "$REQUIRED_CAPABILITY"; then
    echo "Fehler: Backend-Faehigkeit '$REQUIRED_CAPABILITY' fehlt nach dem Update." >&2
    journalctl -u scrapper-web.service -n 80 --no-pager >&2 || true
    false
  fi
done

# Prüfe die tatsächlich registrierten FastAPI-Verträge samt HTTP-Methode. Ein
# 404-Smoke gegen Beispiel-IDs wäre mehrdeutig (Route, Auth oder Datensatz) und
# könnte insbesondere eine fehlende zweite Methode auf derselben Route übersehen.
if ! (
  cd "$APP_DIR"
  runuser -u "$APP_USER" -- env \
    SCRAPPER_CONFIG="$APP_DIR/data/config.yaml" \
    "$APP_DIR/venv/bin/python" - <<'PY'
import sys

from app.main import app


required_methods = {
    "/api/admin/pdf/preflight": {"get"},
    "/api/cart/optimize/preview": {"post"},
    "/api/auth/guest": {"post"},
    "/api/auth/register": {"post"},
    "/api/account": {"get"},
    "/api/account/invitations": {"post"},
    "/api/account/invitations/accept": {"post"},
    "/api/recipes/{recipe_id}/save": {"post", "delete"},
    "/api/meal-plan/conductor/preview": {"get", "post"},
    "/api/recipes/{recipe_id}/source-integrity": {"get"},
    "/api/recipes/{recipe_id}/source-integrity/check": {"post"},
    "/api/recipes/{recipe_id}/source-integrity/accept": {"post"},
    "/api/recipes/{recipe_id}/substitutions": {"get"},
    "/api/recipes/{recipe_id}/substitutions/apply": {"post"},
}
paths = app.openapi().get("paths", {})
missing = [
    f"{method.upper()} {path}"
    for path, methods in required_methods.items()
    for method in sorted(methods)
    if method not in paths.get(path, {})
]
if missing:
    print("Fehler: Erforderliche API-Vertraege fehlen:", file=sys.stderr)
    for contract in missing:
        print(f"  - {contract}", file=sys.stderr)
    raise SystemExit(1)

print(f"OpenAPI-Gate: {sum(map(len, required_methods.values()))} Methoden registriert.")
PY
); then
  journalctl -u scrapper-web.service -n 80 --no-pager >&2 || true
  false
fi

# Bestehende CT117-Review-Daten werden erst nach erfolgreicher Schema-,
# Dienst-, Capability- und OpenAPI-Prüfung angehoben. Der Fixer validiert die
# isolierte künstliche Instanz erneut, erstellt als APP_USER ein geprüftes
# SQLite-Backup und schreibt URL, Quellenstände, Wochenplan und Einkaufsszenario
# atomar. Jeder
# Fehler bricht durch `set -e` hart ab und lässt das Backup zur Wiederherstellung
# unter data/backups/review-refresh liegen.
if [[ "$IS_REVIEW_INSTANCE" == "1" ]]; then
  # Die neue Version wurde oben bereits gegen Health, Capabilities und OpenAPI
  # geprüft. Für Backup und atomare Demo-Migration wird der einzige
  # schreibende Review-Dienst angehalten, damit zwischen Sicherung und
  # Transaktion kein Wochenplan- oder Konfigurations-Write verloren gehen kann.
  systemctl stop scrapper-web.service
  pushd "$APP_DIR" >/dev/null
  runuser -u "$APP_USER" -- env \
    SCRAPPER_CONFIG="$APP_DIR/data/config.yaml" \
    "$APP_DIR/venv/bin/python" -m tools.refresh_app_review_demo \
    --db "$APP_DIR/data/scrapper.db" \
    --recipe-root "$APP_DIR/files/rezepte" \
    --config "$APP_DIR/data/config.yaml" \
    --backup-dir "$APP_DIR/data/backups/review-refresh" \
    --public-url "https://rezepte-review.mausbaeren.me"
  popd >/dev/null
  systemctl start scrapper-web.service
  REVIEW_HEALTH_FILE="$(mktemp /tmp/rezepte-health-after-review-refresh.XXXXXX)"
  chmod 0600 "$REVIEW_HEALTH_FILE"
  if ! poll_local_health "$REVIEW_HEALTH_FILE"; then
    echo "Fehler: Der Review-Dienst hat nach der Demo-Migration nicht geantwortet." >&2
    journalctl -u scrapper-web.service -n 80 --no-pager >&2 || true
    false
  fi
  REFRESHED_HEALTH_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version", ""))' \
    "$REVIEW_HEALTH_FILE" 2>/dev/null || true)"
  if [[ "$REFRESHED_HEALTH_VERSION" != "$EXPECTED_VERSION" ]]; then
    echo "Fehler: Review-Dienst meldet nach Demo-Migration Version '$REFRESHED_HEALTH_VERSION' statt '$EXPECTED_VERSION'." >&2
    journalctl -u scrapper-web.service -n 80 --no-pager >&2 || true
    false
  fi
fi

# Commit the release with the retired mail timer disabled. Rollback above keeps
# the original timer policy; successful updates never reactivate mail polling.
restore_timer_activity 1
trap - ERR
"$APP_DIR/venv/bin/python" "$APP_DIR/tools/retire_mail_import.py" --apply
rm -rf -- "$APP_DIR/venv.previous" "$APP_DIR/playwright-browsers.previous"
echo "Update erfolgreich. Backend und Frontend laufen gemeinsam auf Version $EXPECTED_VERSION."
echo "Gesundheit: $(cat "$HEALTH_FILE")"
echo "API-Vertraege: OpenAPI-Methoden vollständig registriert"
echo "Rückweg mit Datenbank, Konfiguration und Units: $STATE_DIR"
