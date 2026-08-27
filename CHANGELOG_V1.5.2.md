# Rezepte 1.5.2

## Behoben

- Die dokumentierte Vertrauensgrenze fuer einen separaten cloudflared-LXC
  behaelt dessen Adresse als unmittelbaren TCP-Peer und wertet Forwarded-
  Header erst in der Anwendung aus. Dadurch funktioniert der durch
  Cloudflare Access geschuetzte Login auch bei `auth_disabled: true`, ohne
  direkte LAN-Clients freizuschalten.
