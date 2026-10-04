# 1.8.4 — Importwiederholungen und Bildveröffentlichung

- Eine erneute Linkübernahme erhält bereits ermittelte Beschreibungen,
  Zutaten-/Schrittvorschläge sowie Video- und Fotoreferenzen. Das gilt auch,
  wenn der bisherige Auftrag während der Wiederholung endet und das Kontingent
  einen weiteren Auftrag ablehnt. Neue Platzhalter überschreiben keine Daten.
- Bildveröffentlichung und kompensierender Rollback werden zusätzlich durch
  eine Betriebssystem-Dateisperre geschützt. Ein fehlgeschlagener Prozess
  kann den erfolgreichen Bildwechsel eines anderen Prozesses nicht zurückrollen.
- Versteckte Bild-/PDF-Entwürfe und Rückfallkopien werden bei der Titelbildsuche
  und bei der Originalsicherung ignoriert. Normale Bilder bleiben verfügbar.
- Bilduploads verarbeiten die Bilddaten außerhalb der HTTP-Ereignisschleife;
  andere HTTP-Anfragen bleiben währenddessen verfügbar. Sicherung, Bildwechsel
  und Vorschaubilder verwenden denselben gesperrten Stand. Ein belegtes Bild
  führt nach begrenzter Wartezeit zu 409 und kann erneut versucht werden.
- Datenbankschema bleibt 263; bestehende Konfiguration und Daten werden erhalten.
