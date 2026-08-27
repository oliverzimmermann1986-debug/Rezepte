# Rezepte 1.5.6

## Behoben

- TikTok-Videos können auf dem Proxmox-Server wieder temporär geladen werden,
  wenn TikTok eine Browser-TLS-Signatur voraussetzt. Dafür wird die von yt-dlp
  empfohlene `curl_cffi`-Imitation nun mitinstalliert.
- Zutaten und Schritte aus Videoframes beziehungsweise der Audiospur erreichen
  dadurch wieder den vorhandenen KI-Fallback. Das Video selbst bleibt temporär
  und wird weiterhin weder gespeichert noch an die iPhone-App ausgeliefert.
- Das Proxmox-Update bricht künftig ab, falls yt-dlp nach dem Venv-Tausch kein
  verfügbares Browser-Imitationsziel meldet, statt eine scheinbar gesunde,
  aber für solche TikToks unvollständige Version zu starten.
