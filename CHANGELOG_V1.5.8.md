# Rezepte 1.5.8

## Behoben

- Enthält den Fix aus 1.5.7 für „Nochmals mit KI prüfen“ bei älteren TikTok-
  und Instagram-Prüfeinträgen ohne Link-Marker.
- Das atomare Proxmox-Update liest die erwartete Versionsnummer nun aus der
  kanonischen Paketversion. Dadurch akzeptiert das Sicherheitsgate den seit
  1.5.7 entkoppelten API-Versionswert und rollt ein gesundes Update nicht mehr
  fälschlich zurück.
