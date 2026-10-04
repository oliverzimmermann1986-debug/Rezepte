# 1.8.6 – Request-Performance, Video-Archiv und Expo-Barrierefreiheit

- Authentifizierungsabhängigkeiten lesen SQLite im Worker und verwenden pro
  Request denselben geprüften Benutzer. Offene SSE-Verbindungen prüfen eine
  Sperre oder einen Sitzungswiderruf weiterhin erneut.
- Authentifizierte Thumbnails dürfen privat gespeichert werden, müssen vor
  Wiederverwendung aber die Sitzung prüfen und den ETag revalidieren. Andere
  private API-Antworten bleiben `no-store`.
- Warenkorbzugriffe ohne fällige Wiederholung vermeiden die Schreibsperre.
  Fällige Wiederholungen bleiben atomar und an den Haushalt gebunden.
- Die Suche ermittelt Kandidaten-IDs einmal statt Zutaten für jede äußere
  Rezeptzeile erneut abzufragen. Synonyme, Teilwörter und Ausschlüsse bleiben
  erhalten; Teilwortsuche benötigt weiterhin Scans.
- Restore lehnt auch bei aktiven Job- oder Backup-Timern ab.
- Der Archiver installiert seine venv an einem dauerhaften Pfad und verbindet
  sie per Symlink. Installation startet keinen Download zur Freigabeprüfung.
  Rollback stellt Code, venv, Requirements, Units und einen aktiven Timer wieder
  her. Das Zeitbudget der Unit umfasst vier Downloads einschließlich Reserve.
  Updates bewahren vorhandene Workerargumente und Timerzyklen; größere
  konfigurierte Jobmengen erhalten ein entsprechend größeres Zeitbudget.
- Ein normaler Abbruch des Archivierungsprozesses bereinigt Teildateien und
  gibt den Auftrag für einen erneuten Versuch frei.
- Expo: Displayschutz während des fokussierten Kochmodus, VoiceOver-Status und
  Bewertung an Rezeptkarten, mehr semantische Überschriften und lesbare lange
  Rezepttexte bei großer Systemschrift. Diese Änderungen benötigen einen
  neuen App-Build und eine Geräteabnahme.
