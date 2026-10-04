# Audit-Nacharbeit 1.8.4

Historischer Bericht. Aktueller geprüfter Backend/Webstand:
[1.8.5](AUDIT_FOLLOWUP_1.8.5.md).

Stand: 04.10.2026. Backend/Web **1.8.4**, Schema **263**, ist nach erfolgreicher
Prüfung auf Review CT117 auf Produktion CT200 bereitgestellt und am laufenden
Dienst geprüft. Review ist wieder ausgeschaltet und nicht eingehängt.

## Nachgewiesene Fehler und Korrekturen

1. **Ein erneuter Linkimport verwarf bereits ermittelte Inhalte.** Ein aktiver
   Auftrag erhielt beim erneuten Einreichen einen leeren Platzhalter, der
   Beschreibung, Zutaten-/Schrittvorschläge und Medienreferenzen überschrieb.
   Auch eine anschließend mit 429 abgelehnte Wiederholung konnte zuvor Daten
   verändern. Platzhalter werden jetzt atomar nur angelegt, wenn noch kein
   Eintrag existiert. Worker können ihre Ergebnisse weiterhin aktualisieren.
   Quellen: `app/db.py:1500`, `app/routes/api_pending.py:326`.
2. **Ein fehlgeschlagener Bildwechsel überschrieb einen erfolgreichen anderen
   Prozess.** Die bisherige Sperre galt nur innerhalb eines Python-Prozesses.
   Ein zweiter Prozess konnte ein Bild veröffentlichen, bevor der erste seine
   fehlgeschlagene Änderung zurückrollte. Der Rollback stellte danach das
   ältere Bild wieder her. Veröffentlichung, Rückfallkopie und Commit halten
   jetzt gemeinsam eine Betriebssystem-Dateisperre je Rezeptordner. Sperren
   sind innerhalb desselben Threads wiedereintrittsfähig, werden bei Fehlern
   freigegeben und haben eine begrenzte Wartezeit. Quelle:
   `app/recipes/image_cache.py:45`.
3. **Unveröffentlichte Bilder und PDFs wurden als Titelbild ausgeliefert.**
   Ohne gespeicherten Titelbildnamen fand die Fallback-Suche auch versteckte
   Entwürfe und Rollback-Dateien. Die Originalsicherung konnte einen solchen
   Entwurf zusätzlich zum Original erklären. Beide Suchpfade ignorieren jetzt
   versteckte Dateien. Sichtbare normale Bilder, Vorschaubilder und deren
   Sicherung bleiben verfügbar. Quellen: `app/routes/api_recipes.py:2651`,
   `app/recipes/image_generation.py:131`.
4. **Ein Bildupload blockierte andere HTTP-Anfragen.** Bilddekodierung,
   Versionssicherung, Dateisperren und Vorschauberechnung liefen synchron in
   der HTTP-Ereignisschleife. Diese Arbeiten laufen jetzt in einem Workerthread.
   Sicherung, Bildwechsel und Cache halten dieselbe Ordnersperre; unmittelbar
   davor werden Rezept und Ordner erneut geprüft. Ein blockierter Bildwechsel
   antwortet nach begrenzter Wartezeit mit 409. Quelle:
   `app/routes/api_recipes.py:1980`.

Vor der Korrektur schlugen sechs neue Prüfungen für Importwiederholungen,
versteckte Dateien und den Rollback zwischen zwei Prozessen fehl. Eine siebte
Prüfung wies die Blockade der HTTP-Ereignisschleife nach. Zusätzlich prüfen die
neuen Tests normale Bild-Fallbacks, versteckte PDF-Entwürfe, verschachtelte
Sperren und die Freigabe nach einem Fehler. Insgesamt sind elf neue Fälle
enthalten. Die abschließende gezielte Prüfung mit Versionswiederherstellung
bestand mit **12 Tests**.

Der erste vollständige Lauf fand eine während der Auslagerung eingeführte
falsche Variablenreferenz in der Upload-Protokollierung. Beide betroffenen
Uploadtests bestanden nach der Korrektur. Der endgültige Backendlauf wird
gesondert dokumentiert; der fehlerhafte Lauf ist kein Freigabenachweis.

## Prüfungen und Grenzen

- Gezielte Hauptprüfung: **103 bestanden**; nach den ergänzten Sperr- und
  PDF-Prüfungen **32 bestanden**. Abschließende Upload-/Importprüfung:
  **12 bestanden**.
- Web: **32 bestanden**. Expo: **25 bestanden**.
- Review und Produktion: Version 1.8.4, SQLite-Integrität, Fremdschlüssel, Loginseiten,
  Gastzugang, acht Schreib-/Adminverbote und Druck-PDF bestanden. Alle
  **131 installierten Paketdateien** sind mit dem Manifest verglichen.
- Beide Linux-Container: synthetische Prüfung für Haushaltswechsel, persönliche
  Identitäten, Kontingente und Freigaben bestanden. Zusätzlich wurden
  Importwiederholungen, versteckte Bildentwürfe und der kompensierende
  Bild-Rollback mit zwei tatsächlichen Prozessen erfolgreich geprüft.
- Vollständiger isolierter Backendlauf: **800 bestanden, 2 übersprungen** in
  691,63 s. Die beiden OCR-Tests benötigen lokal nicht installiertes Tesseract.
- Produktion: alle **229 aktiven globalen Rezepte** und ursprünglichen Werte
  in **22 geprüften Tabellen** erhalten. Kein Rezept entfernt und keine
  Quellenmetadaten geändert. Bestehende Konten und Konfiguration erhalten.
  Der historische unklar zugeordnete Kochzwischenstand bleibt mit seinen
  ursprünglichen Feldern unzugeordnet; keine Abschlusswiederholungen vorhanden.
- `/healthz` und `/readyz` antworten nach dem Rollout mit **200**, `ok: true`
  und Version **1.8.4**. Produktionsdienst und beide bisherigen Timer aktiv.
  Keine aktiven Importaufträge, privaten Waisen oder neuen realen Konten bzw.
  Einladungen durch diese Prüfungen.
- Externe KI-Aufrufe und echte neue Importe wurden nicht durchgeführt.
  Keine nativen Clientänderungen in dieser Runde. Xcode, XCTest, Simulator,
  IPA-Erstellung und TestFlight wurden hier nicht ausgeführt.
- Kontoselbstlöschung mit geregelter Datenbereinigung und die frühere CSP mit
  `unsafe-eval` bleiben offen. Die bereits dokumentierten separaten Admin-,
  Mail- und CLI-Jobs verwenden weiterhin ihre eigenen Berechtigungen.

## Paket und Arbeitsstand

Paket: `.tmp/releases/1.8.4-security/rezepte-1.8.4.tar.gz`.
SHA-256: `dea1e7d929e5be4aaa600523b0c3213d8d93aa03219d3057f5a8283be67b9707`.
Enthalten: **131 Dateien**, Schema 263, Version 1.8.4.
Lokaler endgültiger Testnachweis:
`.tmp/backend-final-1.8.4.xml`.
Weitere Nachweise im Paketordner: `ct117-report.json`, `ct200-report.json`
und `checks.json`. Abschließende Livechecks: `.tmp/final-live-1.8.4.log`
und `.tmp/final-accounts-1.8.4.log`.

Rollback-Snapshots einschließlich Code, Datenbank, Konfiguration und Units:

- Produktion: `/opt/scrapper-code-backups/state-20261004-134837-16758`
- Review: `/opt/scrapper-code-backups/state-20261004-133703-331`

Integrierter Arbeitsstand:
`C:\Users\Entwickler\.codex\worktrees\household-release\Rezepte`.
Der ursprüngliche Checkout bleibt erhalten. Kein Commit, Push oder Branchwechsel.
