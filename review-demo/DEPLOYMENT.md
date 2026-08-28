# Isolierte Rezepte-App-Review-Umgebung

Zielzustand:

- frischer LXC `117` mit Hostname `rezepte-review`, ohne Bind-Mounts;
- ausschließlich künstliche Rezepte und generierte Food-Fotos;
- isolierter Administrator `app-review`, dessen zufälliges Passwort nur in einer
  root-lesbaren Datei liegt;
- kein Mailkonto, OpenAI-Schlüssel, Social-Cookie, Webhook, externer Datenträger
  oder Einkauf-Token;
- Scraper-Timer deaktiviert, Webdienst und lokale Datenbanksicherung aktiv;
- öffentlicher nativer App-Zugang unter
  `https://rezepte-review.mausbaeren.me`, ohne Cloudflare-Access-Browserlogin;
- App-eigene Anmeldung und Login-Rate-Limit bleiben aktiv.

## Einmaliger Ablauf

1. Einen frischen unprivilegierten Debian-LXC ohne Mounts erstellen.
2. Repository und Abhängigkeiten mit `proxmox/install.sh` installieren.
3. Sicherstellen, dass der Hostname exakt `rezepte-review` ist.
4. `proxmox/setup-review-instance.sh` einmal als root ausführen.
   Standardmäßig wird die cloudflared-LXC-IP `192.168.1.141` als direkter,
   vertrauenswürdiger Proxy gesetzt und Port `8000` für diesen Tunnel an alle
   Container-Interfaces gebunden. Bei abweichender Topologie vorher
   `REVERSE_PROXY_IP` setzen und den Port per Firewall auf diesen Peer begrenzen.
5. Im Cloudflare-Tunnel-Hub einen öffentlichen Hostname auf die neue LXC-IP und
   Port `8000` routen. Für den nativen Client keine interaktive Access-Policy
   vorschalten.
6. Lokal, über LAN und öffentlich prüfen: `/healthz`, `/privacy`, Login,
   Rezeptbilder, Details, Wochenplan und Einkaufsliste.
7. Das Passwort aus `/root/rezepte-app-review-credentials.txt` geschützt in App
   Store Connect eintragen. Nicht ins Repository, in Logs oder ins Video kopieren.

Der Produktionscontainer `200` ist keine Clone-Quelle: er bindet echte Hostdaten
unter `/mnt/media-nas` und `/srv/video-archive` ein. Ein frischer Container ist
die Sicherheitsgrenze zwischen App Review und Produktion.
