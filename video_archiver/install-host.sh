#!/usr/bin/env bash
# Installiert ausschließlich den privaten Archiver. Rezept-Anwendung und
# Laufzeitdaten werden nicht verändert.
set -Eeuo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${VIDEO_ARCHIVER_INSTALL_DIR:-/opt/video-archiver}"
STATE_DIR="${VIDEO_ARCHIVER_STATE_DIR:-/var/lib/video-archiver}"
ARCHIVE_DIR="${VIDEO_ARCHIVER_ARCHIVE_DIR:-/srv/video-archive}"
SERVICE_USER="${VIDEO_ARCHIVER_USER:-videoarchive}"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Fehler: Bitte als root ausführen." >&2
  exit 1
fi
if [[ ! -f "$SOURCE_DIR/video_archiver/__main__.py" ]]; then
  echo "Fehler: video_archiver-Paket fehlt im Release." >&2
  exit 1
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$STATE_DIR" --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  apt-get update
  apt-get install -y --no-install-recommends ffmpeg
fi

install -d -m 0755 -o root -g root "$INSTALL_DIR"
install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$STATE_DIR" "$ARCHIVE_DIR"

rm -rf "$INSTALL_DIR/video_archiver"
cp -a "$SOURCE_DIR/video_archiver" "$INSTALL_DIR/video_archiver"
find "$INSTALL_DIR/video_archiver" -type d -exec chmod 0755 {} +
find "$INSTALL_DIR/video_archiver" -type f -exec chmod 0644 {} +
chown -R root:root "$INSTALL_DIR/video_archiver"

python3 -m venv "$INSTALL_DIR/venv" --upgrade-deps
"$INSTALL_DIR/venv/bin/pip" install --disable-pip-version-check --upgrade yt-dlp

install -m 0644 "$SOURCE_DIR/systemd/video-archiver.service" /etc/systemd/system/video-archiver.service
install -m 0644 "$SOURCE_DIR/systemd/video-archiver.timer" /etc/systemd/system/video-archiver.timer
systemctl daemon-reload
systemctl enable --now video-archiver.timer

(cd "$INSTALL_DIR" && sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/python" -m video_archiver \
  --queue "$STATE_DIR/queue.db" status)
systemctl start video-archiver.service

echo "Video-Archiver installiert."
echo "Queue:   $STATE_DIR/queue.db"
echo "Archiv:  $ARCHIVE_DIR"
echo "Timer:   video-archiver.timer"
