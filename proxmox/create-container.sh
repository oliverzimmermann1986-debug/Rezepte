#!/usr/bin/env bash
# ============================================================
# Proxmox LXC Container Erstellungs-Script
# Führe das auf dem Proxmox Host aus (nicht im Container!)
# ============================================================
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "❌ Dieses Script muss als root auf dem Proxmox-Host laufen." >&2
  exit 1
fi
for command in pct pveam python3; do
  command -v "$command" >/dev/null 2>&1 || { echo "❌ Befehl fehlt: $command" >&2; exit 1; }
done

# --------- KONFIGURATION (anpassen!) ---------
CTID="${CTID:-200}"
HOSTNAME="${HOSTNAME:-scrapper}"
PASSWORD_WAS_GENERATED=0
if [[ -z "${PASSWORD:-}" ]]; then
  PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
  PASSWORD_WAS_GENERATED=1
fi
PASSWORD_FILE="${PASSWORD_FILE:-./scrapper-${CTID}-root-password.txt}"
STORAGE="${STORAGE:-local-lvm}"
DISK_SIZE="${DISK_SIZE:-16}"
MEMORY="${MEMORY:-2048}"
SWAP="${SWAP:-512}"
CORES="${CORES:-2}"
BRIDGE="${BRIDGE:-vmbr0}"
IP_ADDR="${IP_ADDR:-dhcp}"             # z.B. "192.168.178.50/24,gw=192.168.178.1"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
NESTING="${NESTING:-0}"
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

# Optionale Bind-Mounts. Standardmäßig läuft die App komplett im Container.
# Wenn du Host-Verzeichnisse durchreichen willst (z.B. NAS, externe Disk,
# bestehendes Medien-Verzeichnis), setze diese ENV-Vars vor dem Skript-Aufruf:
#   MOUNT0_HOST=/path/on/host MOUNT0_CT=/mnt/data ./create-container.sh
#   MOUNT1_HOST=/another/host MOUNT1_CT=/mnt/foo  ./create-container.sh
MOUNT0_HOST="${MOUNT0_HOST:-}"
MOUNT0_CT="${MOUNT0_CT:-/mnt/data}"
MOUNT1_HOST="${MOUNT1_HOST:-}"
MOUNT1_CT="${MOUNT1_CT:-/mnt/medien}"

normalize_mnt_subdir() {
  local raw="$1" normalized
  [[ -n "$raw" && "$raw" != *$'\n'* && "$raw" != *$'\r'* && "$raw" != *','* ]] || return 1
  normalized="$(readlink -m -- "$raw")" || return 1
  [[ "$normalized" == /mnt/* && "$normalized" != /mnt ]] || return 1
  printf '%s\n' "$normalized"
}

[[ "$CTID" =~ ^[1-9][0-9]{2,8}$ ]] || { echo "❌ CTID muss numerisch und mindestens dreistellig sein" >&2; exit 1; }
[[ "$HOSTNAME" =~ ^[A-Za-z0-9][A-Za-z0-9.-]{0,62}$ ]] || { echo "❌ Ungültiger Hostname" >&2; exit 1; }
[[ "$STORAGE" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$ ]] || { echo "❌ Ungültiger STORAGE-Name" >&2; exit 1; }
[[ "$TEMPLATE_STORAGE" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$ ]] || { echo "❌ Ungültiger TEMPLATE_STORAGE-Name" >&2; exit 1; }
[[ "$BRIDGE" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$ ]] || { echo "❌ Ungültiger Bridge-Name" >&2; exit 1; }
[[ "$DISK_SIZE" =~ ^[1-9][0-9]{0,4}$ ]] || { echo "❌ DISK_SIZE muss eine positive Ganzzahl sein" >&2; exit 1; }
[[ "$MEMORY" =~ ^[1-9][0-9]{2,7}$ ]] || { echo "❌ MEMORY muss eine positive Ganzzahl in MB sein" >&2; exit 1; }
[[ "$SWAP" =~ ^[0-9]{1,7}$ ]] || { echo "❌ SWAP muss eine nichtnegative Ganzzahl in MB sein" >&2; exit 1; }
[[ "$CORES" =~ ^[1-9][0-9]{0,2}$ ]] || { echo "❌ CORES muss eine positive Ganzzahl sein" >&2; exit 1; }
[[ "$NESTING" == "0" || "$NESTING" == "1" ]] || { echo "❌ NESTING muss 0 oder 1 sein" >&2; exit 1; }
if [[ "$IP_ADDR" != "dhcp" ]]; then
  [[ ${#IP_ADDR} -le 160 && "$IP_ADDR" != -* && "$IP_ADDR" =~ ^[A-Za-z0-9.:,=/+-]+$ ]] \
    || { echo "❌ Ungültige IP_ADDR-Angabe" >&2; exit 1; }
fi
if pct status "$CTID" >/dev/null 2>&1; then
  echo "❌ CTID $CTID existiert bereits. Abbruch ohne Änderungen." >&2
  exit 1
fi
if ! MOUNT0_CT="$(normalize_mnt_subdir "$MOUNT0_CT")"; then
  echo "❌ MOUNT0_CT muss ein sicherer Unterordner von /mnt sein" >&2
  exit 1
fi
if ! MOUNT1_CT="$(normalize_mnt_subdir "$MOUNT1_CT")"; then
  echo "❌ MOUNT1_CT muss ein sicherer Unterordner von /mnt sein" >&2
  exit 1
fi
for mount_host in "$MOUNT0_HOST" "$MOUNT1_HOST"; do
  if [[ -n "$mount_host" ]] && { [[ "$mount_host" != /* ]] || [[ "$mount_host" == *","* ]] \
      || [[ "$mount_host" == *$'\n'* ]] || [[ "$mount_host" == *$'\r'* ]]; }; then
    echo "❌ Host-Mountpfade müssen absolute Pfade ohne Komma/Steuerzeichen sein: $mount_host" >&2
    exit 1
  fi
done

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

# Container erstellen. Nesting ist für die App nicht nötig und bleibt aus.
FEATURE_ARGS=()
if [[ "$NESTING" == "1" ]]; then
  FEATURE_ARGS+=(--features "nesting=1")
fi
pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" \
  --hostname "$HOSTNAME" \
  --password "$PASSWORD" \
  --storage "$STORAGE" \
  --rootfs "$STORAGE:$DISK_SIZE" \
  --memory "$MEMORY" \
  --swap "$SWAP" \
  --cores "$CORES" \
  --net0 "$NET_CONFIG" \
  --unprivileged 1 \
  --onboot 1 \
  --start 0 \
  "${FEATURE_ARGS[@]}"

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
NETWORK_OK=0
for i in {1..15}; do
  if pct exec "$CTID" -- ping -c 1 -W 2 deb.debian.org >/dev/null 2>&1; then
    NETWORK_OK=1
    break
  fi
  sleep 2
done
if [[ $NETWORK_OK -ne 1 ]]; then
  echo "⚠️  Container läuft, aber der Internet-Test war nach 30 Sekunden noch nicht erfolgreich." >&2
fi

if [[ $PASSWORD_WAS_GENERATED -eq 1 ]]; then
  umask 077
  printf '%s\n' "$PASSWORD" > "$PASSWORD_FILE"
fi

echo ""
echo "✅ Container $CTID erstellt und läuft."
if [[ $PASSWORD_WAS_GENERATED -eq 1 ]]; then
  echo "🔑 Root-Passwort sicher gespeichert in: $PASSWORD_FILE"
else
  echo "🔑 Root-Passwort wurde über die Umgebungsvariable PASSWORD vorgegeben."
fi
echo ""
echo "Nächste Schritte:"
echo "  1. In den Container einloggen:    pct enter $CTID"
echo "  2. Installations-Script ausführen: bash /root/install.sh"
echo ""
echo "Oder direkt installieren:"
echo "  pct push $CTID ./install.sh /root/install.sh"
echo "  pct exec $CTID -- bash /root/install.sh"
