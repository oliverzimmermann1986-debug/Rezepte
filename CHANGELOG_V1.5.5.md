# Rezepte 1.5.5

## Behoben

- TikTok-Foto- und Slideshow-Posts lesen ihre vollständige Caption nun zuerst
  aus der strukturierten Antwort des offiziellen TikTok Embed Players.
- Kurzlinks wie `vm.tiktok.com/…` werden automatisch bis zur Beitrags-ID
  aufgelöst. Dadurch funktioniert der Import auch dann, wenn TikToks normale
  Beitragsseite nur ein Slider-CAPTCHA statt der Caption zeigt.
- Der bisherige aufgeklappte Browsertext bleibt als Rückfall erhalten; CAPTCHA-
  Text wird erkannt und nicht versehentlich als Rezeptbeschreibung übernommen.
