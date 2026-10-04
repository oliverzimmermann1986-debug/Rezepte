# 1.8.2 — Haushaltswechsel, Freigaben und Importgrenzen

- Freigabelinks gehören dauerhaft zu einem Haushalt. Namenwechsel oder erneute
  Registrierung nach dem Löschen einer Anmeldung übertragen keine fremden Rechte.
  Beim Haushaltsbeitritt ziehen Freigaben mit; die verbleibende Person behält sie.
- Haushaltsänderungen und Importe sind über Betriebssystem-Sperren zwischen
  Prozessen geschützt. Eine veraltete Haushaltszuordnung wird vor dem Schreiben
  abgewiesen. Datei-, Foto- und Einkaufsänderungen verwenden dieselbe Grenze.
- Das letzte Mitglied eines Haushalts mit Daten oder laufenden Importen lässt
  sich nicht mehr löschen. Die Daten bleiben samt Eigentümer erhalten.
- Explizit globale Linkimporte behalten ihre Sichtbarkeit auch bei Übergabe
  durch die Haushaltsfassade an die persistente Queue.
- Nutzeranalysen sind auf 20 je Haushalt und 200 auf dem Server innerhalb von
  24 Stunden begrenzt, konfigurierbar per YAML. Globale Verknüpfungen und Replays
  bleiben frei. Gastanmeldungen haben ein gemeinsames API-/Browser-Limit.
- Datenbankschema 262 erhält bestehende Freigaben. Bei unklarem historischem
  Ersteller bleiben die Verwaltungsrechte unzugeordnet; bestehende signierte
  Links behalten ihre Laufzeit und ihren Widerrufsstatus.

Die eigentliche Kontoselbstlöschung mit geregelter Datenbereinigung bleibt offen.
