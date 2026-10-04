# 1.8.1 – Haushaltsgrenzen und ehrliche Rezeptzustände

- Der authentifizierte PDF-Export unter `/recipe/{id}/pdf` prüft denselben
  Haushalt wie die Rezept-API. Gäste und fremde Haushalte erhalten für private
  Rezepte 404, bevor die PDF-Erzeugung beginnt. Globale PDFs bleiben lesbar.
- Freigabelinks globaler Rezepte sind nur im erzeugenden Haushalt sichtbar
  und widerrufbar. Öffentliche Freigaben benötigen weiterhin ein gültiges,
  widerrufbares signiertes Token.
- Job-Livestreams verlangen Administratorrechte; die Prüfung wird während
  einer offenen Verbindung wiederholt. Andere Konten und Gäste öffnen weder
  einen Stream noch dessen Polling-Ersatz.
- Abgelehnte Browser-Anmeldungen protokollieren weder eingegebene Benutzernamen
  noch IP-Adressen.
- Web-, Expo- und SwiftUI-Rezeptkarten zeigen ausstehende, fehlgeschlagene,
  übersprungene und unvollständige Auswertungen getrennt. „Kochfertig“ erfordert
  eine erfolgreiche Auswertung, Zutaten, Schritte und keinen Pflegehinweis.
- Statusfarben im Web entsprechen den dunkleren semantischen Expo-Farben.
  Favoriten und native Entfernen-Schaltflächen haben mindestens 44 Pixel/Punkte.
- Bestehende DB-Migration mit Tupelzeilen, Bestandsbenutzern und Versionen
  231/232/260 ist durch Regressionstests abgesichert. Schema bleibt 261.
- Lange Beschreibungen, Zutatenzeilen und Zubereitungsschritte im Rezept-PDF
  werden über mehrere Seiten umgebrochen, ohne Text zu verlieren.
- Updates und Rückfälle vergleichen Dateien per Prüfsumme. Die installierten
  Release-Dateien werden vor dem Dienststart gegen das Paketmanifest geprüft;
  Python-Bytecode nutzt Inhalts-Hashes statt gleicher Zeitstempel und Dateigrößen.
