# Rezepte 1.5.3

## Behoben

- TikTok-Foto- und Slideshow-Posts erhalten bereits beim ersten Import die
  vollständig gerenderte Caption. Eine leere oder gekürzte yt-dlp-Beschreibung
  wird durch den im Browser aufgeklappten Text ersetzt.
- Direkte TikTok-Links mit `/photo/…` werden dem richtigen Beitrag zugeordnet;
  Video- und Foto-Posts nutzen damit denselben abgesicherten Caption-Pfad.
