#!/usr/bin/env bash
# ============================================================
# Proxmox LXC Container Erstellungs-Script
# Führe das auf dem Proxmox Host aus (nicht im Container!)
# ============================================================
set -euo pipefail

# --------- KONFIGURATION (anpassen!) ---------
CTID="${CTID:-200}"
HOSTNAME="${HOSTNAME:-scrapper}"
PASSWORD="${PASSWORD:-changeme}"
STORAGE="${STORAGE:-local-lvm}"
DISK_SIZE="${DISK_SIZE:-16}"
MEMORY="${MEMORY:-2048}"
SWAP="${SWAP:-512}"
CORES="${CORES:-2}"
BRIDGE="${BRIDGE:-vmbr0}"
IP_ADDR="${IP_ADDR:-dhcp}"             # z.B. "192.168.178.50/24,gw=192.168.178.1"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
# Template: wenn nicht via Env gesetzt, automatisch neueste Debian-12-Version finden
TEMPLATE="${TEMPLATE:-}"
if [[ -z "$TEMPLATE" ]]; then
  pveam update >/dev/null
  TEMPLATE=$(pveam available --section system 2>/dev/null \
    | awk '/debian-12-standard/ {print $2}' | sort -V | tail -n1)
  if [[ -z "$TEMPLATE" ]]; then
    echo "❌ Konnte kein debian-12-Template finden. Setze z.B. TEMPLATE=debian-12-standard_12.12-1_amd64.tar.zst" >&2
    exit 1
  fi
  echo "▶️  Template automatisch gewählt: $TEMPLATE"
fi

# NAS Mount-Point (optional - für rclone-sync)
NAS_MOUNT_HOST="${NAS_MOUNT_HOST:-/mnt/media-nas}"
NAS_MOUNT_CT="${NAS_MOUNT_CT:-/mnt/media-nas}"

# Medien-Verzeichnisse (für TikTok Scraper)
MEDIA_MOUNT_HOST="${MEDIA_MOUNT_HOST:-}"   # z.B. /mnt/pve/medien - leer = nicht mounten
MEDIA_MOUNT_CT="${MEDIA_MOUNT_CT:-/mnt/medien}"

echo "▶️  Erstelle LXC Container $CTID ($HOSTNAME)"

# Template laden falls nicht vorhanden
if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE"; then
  echo "📥 Lade Template $TEMPLATE..."
  pveam update
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi

# Network-Config
NET_CONFIG="name=eth0,bridge=$BRIDGE"
if [[ "$IP_ADDR" == "dhcp" ]]; then
  NET_CONFIG="$NET_CONFIG,ip=dhcp"
else
  NET_CONFIG="$NET_CONFIG,ip=$IP_ADDR"
fi

# Container erstellen
pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
  --hostname "$HOSTNAME" \
  --password "$PASSWORD" \
  --storage "$STORAGE" \
  --rootfs "$STORAGE:$DISK_SIZE" \
  --memory "$MEMORY" \
  --swap "$SWAP" \
  --cores "$CORES" \
  --net0 "$NET_CONFIG" \
  --features "nesting=1" \
  --unprivileged 1 \
  --onboot 1 \
  --start 0

# Bind-Mounts (NAS + optional Medien)
if [[ -d "$NAS_MOUNT_HOST" ]]; then
  echo "🔗 Bind-Mount: $NAS_MOUNT_HOST → $NAS_MOUNT_CT"
  pct set "$CTID" -mp0 "$NAS_MOUNT_HOST,mp=$NAS_MOUNT_CT"
fi

if [[ -n "$MEDIA_MOUNT_HOST" ]] && [[ -d "$MEDIA_MOUNT_HOST" ]]; then
  echo "🔗 Bind-Mount: $MEDIA_MOUNT_HOST → $MEDIA_MOUNT_CT"
  pct set "$CTID" -mp1 "$MEDIA_MOUNT_HOST,mp=$MEDIA_MOUNT_CT"
fi

# Container starten
echo "▶️  Starte Container..."
pct start "$CTID"
sleep 5

# Netzwerk testen
echo "🌐 Warte auf Netzwerk..."
for i in {1..15}; do
  if pct exec "$CTID" -- ping -c 1 -W 2 deb.debian.org >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo ""
echo "✅ Container $CTID erstellt und läuft."
echo ""
echo "Nächste Schritte:"
echo "  1. In den Container einloggen:    pct enter $CTID"
echo "  2. Installations-Script ausführen: bash /root/install.sh"
echo ""
echo "Oder direkt installieren:"
echo "  pct push $CTID ./install.sh /root/install.sh"
echo "  pct exec $CTID -- bash /root/install.sh"
