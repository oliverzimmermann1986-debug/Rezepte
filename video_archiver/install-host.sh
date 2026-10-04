#!/usr/bin/env bash
# Installiert ausschließlich den privaten Archiver. Rezept-Anwendung und
# Laufzeitdaten werden nicht verändert.
set -Eeuo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${VIDEO_ARCHIVER_INSTALL_DIR:-/opt/video-archiver}"
STATE_DIR="${VIDEO_ARCHIVER_STATE_DIR:-/var/lib/video-archiver}"
ARCHIVE_DIR="${VIDEO_ARCHIVER_ARCHIVE_DIR:-/srv/video-archive}"
SERVICE_USER="${VIDEO_ARCHIVER_USER:-videoarchive}"
UNIT_DIR="${VIDEO_ARCHIVER_UNIT_DIR:-/etc/systemd/system}"
INSTALL_DIR="$(realpath -m "$INSTALL_DIR")"
STATE_DIR="$(realpath -m "$STATE_DIR")"
ARCHIVE_DIR="$(realpath -m "$ARCHIVE_DIR")"
UNIT_DIR="$(realpath -m "$UNIT_DIR")"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Fehler: Bitte als root ausführen." >&2
  exit 1
fi
if [[ ! -f "$SOURCE_DIR/video_archiver/__main__.py" ]]; then
  echo "Fehler: video_archiver-Paket fehlt im Release." >&2
  exit 1
fi
for candidate in "$INSTALL_DIR" "$STATE_DIR" "$ARCHIVE_DIR" "$UNIT_DIR"; do
  if [[ "$candidate" != /* || "$candidate" == "/" || "$candidate" == "/opt" \
        || "$candidate" == "/var" || "$candidate" == "/srv" || "$candidate" == "/usr" || "$candidate" == "/etc" ]]; then
    echo "Fehler: unsicherer Zielpfad: $candidate" >&2
    exit 1
  fi
done

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$STATE_DIR" --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  apt-get update
  apt-get install -y --no-install-recommends ffmpeg
fi

install -d -m 0755 -o root -g root "$INSTALL_DIR"
install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$STATE_DIR"
install -d -m 0755 -o "$SERVICE_USER" -g "$SERVICE_USER" "$ARCHIVE_DIR"
install -d -m 0755 "$UNIT_DIR"

STAGE="$(mktemp -d "$INSTALL_DIR/.release.XXXXXX")"
NEW_VENV="$(mktemp -d "$INSTALL_DIR/.venv.XXXXXX")"
chmod 0755 "$NEW_VENV"
RELEASE_READY=0
cleanup_install() {
  rm -rf -- "$STAGE"
  [[ "$RELEASE_READY" == "1" ]] || rm -rf -- "$NEW_VENV"
}
trap cleanup_install EXIT
cp -a "$SOURCE_DIR/video_archiver" "$STAGE/video_archiver"
install -m 0644 "$SOURCE_DIR/video_archiver/requirements.txt" "$STAGE/requirements.txt"
find "$STAGE/video_archiver" -type d -exec chmod 0755 {} +
find "$STAGE/video_archiver" -type f -exec chmod 0644 {} +
chown -R root:root "$STAGE"
# Console scripts keep this absolute interpreter path throughout the release.
python3 -m venv "$NEW_VENV"
"$NEW_VENV/bin/pip" install --disable-pip-version-check \
  --requirement "$STAGE/requirements.txt"
"$NEW_VENV/bin/yt-dlp" --version >/dev/null

TIMER_WAS_ACTIVE=0
systemctl is-active --quiet video-archiver.timer && TIMER_WAS_ACTIVE=1
HAD_INSTALL=0
[[ ! -d "$INSTALL_DIR/video_archiver" ]] || HAD_INSTALL=1
for unit in video-archiver.service video-archiver.timer; do
  [[ ! -f "$UNIT_DIR/$unit" ]] || cp -a "$UNIT_DIR/$unit" "$STAGE/$unit.previous"
done
if [[ -f "$INSTALL_DIR/requirements.txt" ]]; then
  cp -a "$INSTALL_DIR/requirements.txt" "$STAGE/requirements.previous"
fi
CODE_REPLACED=0
VENV_REPLACED=0

rollback_install() {
  local rc=$?
  if [[ "$CODE_REPLACED" == "1" ]]; then
    rm -rf -- "$INSTALL_DIR/video_archiver"
    [[ ! -d "$INSTALL_DIR/video_archiver.previous" ]] || mv "$INSTALL_DIR/video_archiver.previous" "$INSTALL_DIR/video_archiver"
  fi
  if [[ "$VENV_REPLACED" == "1" ]]; then
    rm -rf -- "$INSTALL_DIR/venv"
    [[ ! -e "$INSTALL_DIR/venv.previous" ]] || mv "$INSTALL_DIR/venv.previous" "$INSTALL_DIR/venv"
  fi
  for unit in video-archiver.service video-archiver.timer; do
    if [[ -f "$STAGE/$unit.previous" ]]; then
      cp -a "$STAGE/$unit.previous" "$UNIT_DIR/$unit"
    else
      rm -f -- "$UNIT_DIR/$unit"
    fi
  done
  if [[ -f "$STAGE/requirements.previous" ]]; then
    cp -a "$STAGE/requirements.previous" "$INSTALL_DIR/requirements.txt"
  else
    rm -f -- "$INSTALL_DIR/requirements.txt"
  fi
  systemctl daemon-reload || true
  [[ "$TIMER_WAS_ACTIVE" != "1" ]] || systemctl start video-archiver.timer || true
  exit "$rc"
}
trap rollback_install ERR

systemctl stop video-archiver.timer video-archiver.service 2>/dev/null || true
rm -rf -- "$INSTALL_DIR/video_archiver.previous" "$INSTALL_DIR/venv.previous"
[[ ! -d "$INSTALL_DIR/video_archiver" ]] || mv "$INSTALL_DIR/video_archiver" "$INSTALL_DIR/video_archiver.previous"
CODE_REPLACED=1
[[ ! -d "$INSTALL_DIR/venv" ]] || mv "$INSTALL_DIR/venv" "$INSTALL_DIR/venv.previous"
VENV_REPLACED=1
mv "$STAGE/video_archiver" "$INSTALL_DIR/video_archiver"
ln -s "$NEW_VENV" "$INSTALL_DIR/venv"
install -m 0644 "$STAGE/requirements.txt" "$INSTALL_DIR/requirements.txt"

if [[ "$HAD_INSTALL" == "1" && -f "$UNIT_DIR/video-archiver.service" ]]; then
  # Preserve host-specific commands, limits and paths. Only the timing budget
  # and handled-job exit policy change on update.
  "$NEW_VENV/bin/python" - "$UNIT_DIR/video-archiver.service" "$STAGE/service.prepared" <<'PY'
from pathlib import Path
import re
import sys

text = Path(sys.argv[1]).read_text()
logical = text.replace(chr(92) + chr(10), " ")
command = next((line.partition("=")[2] for line in logical.splitlines() if line.startswith("ExecStart=")), "")
def configured_number(flag, default):
    match = re.search(re.escape(flag) + r"(?:=|\s+)(\d+)(?:\s|$)", command)
    if match:
        return max(1, int(match[1]))
    if flag in command:
        raise SystemExit("Unbekanntes Archiver-Zeitbudget; konfigurierte Variablen zuerst auflösen")
    return default
budget = max(3900, configured_number("--max-jobs", 1) * configured_number("--timeout", 900) + 300)
lines = text.splitlines()
start = lines.index("[Service]") + 1
end = next((i for i in range(start, len(lines)) if lines[i].startswith("[")), len(lines))
statuses = {"1"}
for line in lines[start:end]:
    if line.startswith("TimeoutStartSec=") and line.partition("=")[2].strip().isdigit():
        budget = max(budget, int(line.partition("=")[2].strip()))
    if line.startswith("SuccessExitStatus="):
        statuses.update(line.partition("=")[2].split())
body = [line for line in lines[start:end] if not line.startswith(("TimeoutStartSec=", "SuccessExitStatus="))]
body += [f"TimeoutStartSec={budget}", "SuccessExitStatus=" + " ".join(sorted(statuses)), ""]
Path(sys.argv[2]).write_text("\n".join(lines[:start] + body + lines[end:]) + "\n")
PY
  install -m 0644 "$STAGE/service.prepared" "$UNIT_DIR/video-archiver.service"
else
  install -m 0644 "$SOURCE_DIR/systemd/video-archiver.service" "$UNIT_DIR/video-archiver.service"
fi
if [[ "$HAD_INSTALL" == "0" || ! -f "$UNIT_DIR/video-archiver.timer" ]]; then
  install -m 0644 "$SOURCE_DIR/systemd/video-archiver.timer" "$UNIT_DIR/video-archiver.timer"
fi
systemctl daemon-reload

(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/python" -m video_archiver \
  --queue "$STATE_DIR/queue.db" status)
"$INSTALL_DIR/venv/bin/yt-dlp" --version >/dev/null
if [[ "$HAD_INSTALL" == "0" ]]; then
  systemctl enable --now video-archiver.timer
elif [[ "$TIMER_WAS_ACTIVE" == "1" ]]; then
  systemctl start video-archiver.timer
fi

trap - ERR
RELEASE_READY=1
rm -rf -- "$INSTALL_DIR/video_archiver.previous" "$INSTALL_DIR/venv.previous"

echo "Video-Archiver installiert."
echo "Queue:   $STATE_DIR/queue.db"
echo "Archiv:  $ARCHIVE_DIR"
echo "Timer:   video-archiver.timer"
