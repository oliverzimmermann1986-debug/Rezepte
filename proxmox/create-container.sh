#!/usr/bin/env bash
# ============================================================
# Proxmox LXC Container Erstellungs-Script
# Führe das auf dem Proxmox Host aus (nicht im Container!)
# ============================================================
set -euo pipefail

# --------- KONFIGURATION (anpassen!) ---------
CTID="${CTID:-200}"
HOSTNAME="${HOSTNAME:-scrapper}"
PASSWORD="${PASSWORD:-}"
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
if [[ -z "$PASSWORD" || "$PASSWORD" == "changeme" ]]; then
  echo "❌ Setze PASSWORD auf ein starkes, eindeutiges Root-Passwort." >&2
  echo "   Beispiel: PASSWORD='...' ./create-container.sh" >&2
  exit 1
fi
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

# Optionale Bind-Mounts. Standardmäßig läuft die App komplett im Container.
# Wenn du Host-Verzeichnisse durchreichen willst (z.B. NAS, externe Disk,
# bestehendes Medien-Verzeichnis), setze diese ENV-Vars vor dem Skript-Aufruf:
#   MOUNT0_HOST=/path/on/host MOUNT0_CT=/mnt/data ./create-container.sh
#   MOUNT1_HOST=/another/host MOUNT1_CT=/mnt/foo  ./create-container.sh
MOUNT0_HOST="${MOUNT0_HOST:-}"
MOUNT0_CT="${MOUNT0_CT:-/mnt/data}"
MOUNT1_HOST="${MOUNT1_HOST:-}"
MOUNT1_CT="${MOUNT1_CT:-/mnt/medien}"

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

# Optionale Bind-Mounts (nur wenn ENV-Vars gesetzt)
if [[ -n "$MOUNT0_HOST" ]] && [[ -d "$MOUNT0_HOST" ]]; then
  echo "🔗 Bind-Mount: $MOUNT0_HOST → $MOUNT0_CT"
  pct set "$CTID" -mp0 "$MOUNT0_HOST,mp=$MOUNT0_CT"
fi

if [[ -n "$MOUNT1_HOST" ]] && [[ -d "$MOUNT1_HOST" ]]; then
  echo "🔗 Bind-Mount: $MOUNT1_HOST → $MOUNT1_CT"
  pct set "$CTID" -mp1 "$MOUNT1_HOST,mp=$MOUNT1_CT"
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
