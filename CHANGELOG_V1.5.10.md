# Rezepte 1.5.10

## Behoben

- TikTok-Fotoposts liefern beim Import und erneuten Einlesen jetzt ihr erstes
  Bild als Rezeptcover. Dadurch bleiben Karten für Foto-Rezepte nicht länger
  ohne Bild.
- Das erneute Einlesen funktioniert auch dann, wenn `yt-dlp` den Fotopost als
  nicht unterstützte URL meldet.

## Sicherheit

- Cover werden ausschließlich über validierte TikTok-CDN-URLs geladen und vor
  dem Speichern nach Protokoll, Host, MIME-Typ und Dateigröße geprüft.
