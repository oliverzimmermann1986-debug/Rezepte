# Rezepte v1.2.4

## PDF-Route und Update-Sicherheit

- Behebt den Mischstand aus neuem Frontend und noch laufendem altem Backend, der im PDF-Reiter `Not Found` auslöste.
- PDF-Reiter erkennt einen älteren Backend-Prozess und nutzt vorübergehend den kompatiblen Synchronmodus.
- Klare Fehlermeldung, wenn die eigentliche PDF-API vollständig fehlt.
- `/api/system/info` und `/healthz` melden Version und aktivierte Fähigkeiten.
- Neuer lokaler Updater `proxmox/update-local.sh`: überträgt das entpackte Release vollständig, bewahrt Laufzeitdaten, startet den Dienst neu und prüft anschließend Version sowie PDF-Route.
- PWA-Cache auf v1.2.4 angehoben.
