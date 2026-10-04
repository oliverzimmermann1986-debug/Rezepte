# Audit-Nacharbeit 1.8.3

Historischer Nachweis für 1.8.3. Aktueller bereitgestellter Stand:
[Audit-Nacharbeit 1.8.4](AUDIT_FOLLOWUP_1.8.4.md).

Stand: 04.10.2026. Backend/Web **1.8.3** mit Schema **263** ist auf Produktion
CT200 bereitgestellt und am laufenden Dienst geprüft. Derselbe Paketstand
wurde vorher auf Review CT117 geprüft. Review ist wieder ausgeschaltet und
nicht eingehängt.

## Nachgewiesene Fehler und Korrekturen

1. **Kochfortschritt eines gelöschten Kontos war erneut abrufbar.** Fortschritte
   verwendeten nur den Benutzernamen. Nach einer administrativen Löschung und
   erneuten Registrierung zeigte das neue Konto den alten Zwischenstand eines
   globalen Rezepts. Fortschritte speichern jetzt eine feste Benutzer-ID;
   Lesen und Löschen prüfen diese Bindung. Das Löschen des Benutzers entfernt
   dessen persönlichen Zwischenstand atomar per Fremdschlüssel. Quelle:
   `app/db.py:3635`, `app/tenancy.py:214`.
2. **Abschlusswiederholungen gaben fremde Haushaltseinträge zurück.** Derselbe
   Name und Idempotenzschlüssel lieferten nach der Neuregistrierung einen alten
   Abschluss samt Haushaltskennung. Wiederholungen prüfen jetzt Benutzer-ID
   und Haushalt. Unpassende Wiederholungsschlüssel werden durch einen eigenen
   Abschluss ersetzt; die frühere Kochhistorie bleibt im ursprünglichen
   Haushalt. Ein legitimer Haushaltsbeitritt erhält Fortschritt und gültige
   Wiederholungsschlüssel. Quelle: `app/db.py:3719`.
3. **Private Rezeptbilder umgingen das Kontingent.** Ein normales Konto konnte
   auch nach ausgeschöpftem Analysekontingent neue Bildaufträge starten. Einzelne
   Bildaufträge verwenden jetzt dasselbe persistente Kontingent: standardmäßig
   20 Aufträge je Haushalt und 200 auf dem Server über gleitende 24 Stunden.
   Aktive Bildwiederholungen bleiben ohne zusätzlichen Verbrauch möglich.
   Quellen: `app/routes/api_recipes.py:568`, `app/import_budget.py:38`.
4. **Ein gerade beendeter Auftrag konnte eine kostenlose neue Analyse öffnen.**
   Zwischen der Prüfung eines aktiven Duplikats und dem Einreihen konnte der
   Worker den bisherigen Auftrag beenden. Der neue Auftrag blieb ungezählt.
   Eine Wiederholung prüft beim Einreihen in derselben Transaktion erneut:
   aktiven Auftrag zurückgeben oder Kontingent buchen und neuen Auftrag anlegen.
   Dies gilt für Linkimporte, Share-Intakes und Bilder. Ein Fehlschlag beim
   gemeinsamen Speichern von Bildauftrag und Bildstatus rollt auch die neue
   Kontingentbuchung zurück. Quellen: `app/db.py:1874`,
   `app/routes/api_pending.py:322`, `app/routes/api_share.py:208`.

Vor der Korrektur schlugen acht neue Regressionen fehl. Zwei weitere Tests
erzwangen danach den Grenzfall beim Abschluss eines laufenden Auftrags und
bestätigten ihn ebenfalls. Alle diese Fälle bestehen im endgültigen Backendlauf.

## Migration und Daten

Schema 263 ergänzt `user_id` mit `ON DELETE CASCADE` in den Tabellen für
Kochfortschritt und Abschlusswiederholungen. Historische Einträge werden nur
gebunden, wenn das aktuelle Benutzerkonto bereits bei Beginn des Kochlaufs
beziehungsweise bei Abschluss bestand. Fehlende oder erst später angelegte
Konten erhalten keine Rechte an alten Zuständen. Unklare Einträge bleiben
gespeichert und unzugeordnet. Bestehende Felder und Kochhistorien werden bei der
Migration nicht verändert. Die API behält ihr bisheriges Antwortformat.

Die Produktionsvorprüfung fand einen unklar zugeordneten Kochzwischenstand
und keine Abschlusswiederholungen. Die Releaseprüfung vergleicht deshalb
zusätzlich diese beiden Tabellen mit dem gesicherten Ausgangsstand.
Der vorhandene Zwischenstand ist mit allen ursprünglichen Feldern erhalten
und bleibt ohne Benutzerzuordnung. Kein reales Konto oder Einladungslink wurde
für die zusätzlichen Prüfungen erzeugt.

## Prüfungen und Grenzen

- Vollständiger isolierter Backendlauf: **789 bestanden, 2 übersprungen** in
  219,52 s. Die beiden OCR-Tests benötigen lokal nicht installiertes Tesseract.
- Web: **32 bestanden**. Expo: **25 bestanden**.
- Review und Produktion: Migration, SQLite-Integrität, Fremdschlüssel,
  Anmeldung, Gastzugang, acht Schreib-/Adminverbote und Druck-PDF erfolgreich.
- Beide Linux-Container: zusätzliche Prüfung in einer temporären synthetischen
  Datenbank für feste Freigabebesitzer, Benutzerwiedervergabe, Erhalt der
  Haushaltskochhistorie, Abschlusswiederholung nach Beitritt, Sperre zwischen
  Prozessen und gemeinsames Bild-/Analysekontingent bestanden.
- Produktion: alle **229 aktiven globalen Rezepte** und ursprünglichen Werte
  in **22 geprüften Tabellen** erhalten. Bestehende Konten und Zugangsdaten
  unverändert; keine Bereinigung von Rezepten oder historischen Zwischenständen.
- Alle **130 installierten Paketdateien** mit dem Manifest verglichen.
  Produktionsdienst und beide vorhandenen Timer aktiv, `/healthz` und `/readyz`
  mit 200 und Version 1.8.3. Keine aktiven Importjobs oder privaten Waisen.
- Neue Regressionen prüfen Namenswiedervergabe über echte HTTP-Sitzungen,
  Löschung persönlicher Zustände, Erhalt der Haushaltskochhistorie, Migration
  mit Tupelverbindungen und unklaren Altdaten, Fortschritt/Abschluss nach
  Haushaltsbeitritt, serverweite Kontingente bei parallelem Einreihen sowie
  Wiederholungen unmittelbar vor dem Jobabschluss.
- Der Verdacht auf Zugriff normaler Nutzer auf administrative Jobstatus-APIs
  bei der Bildanzeige hat sich in den geprüften Clients nicht bestätigt.
  Die neue Anmeldung im Expo-Client leert den API-Cache vor der Aktivierung.
  Für beide Verdachtsfälle war keine zusätzliche Clientänderung erforderlich.
- Externe KI-Aufrufe und echte neue Importe wurden nicht durchgeführt.
  Keine nativen Clientänderungen in dieser Runde; Xcode, XCTest, Simulator,
  IPA-Erstellung und TestFlight wurden hier nicht ausgeführt.
- Kontoselbstlöschung mit geregelter Datenbereinigung und die frühere CSP mit
  `unsafe-eval` bleiben offen. Gesonderte Admin-Batch-, Mail- und CLI-Jobs
  verwenden weiterhin ihre eigenen Berechtigungen statt dieser Nutzergrenze.

## Paket und Arbeitsstand

Paket: `.tmp/releases/1.8.3-security/rezepte-1.8.3.tar.gz`.
SHA-256: `c337ff4805033c2fd3753d2bec87461666e91bd5dc840524a564f4edafd580fe`.
Enthalten: **130 Dateien**, Schema 263, Version 1.8.3.
Lokaler vollständiger Testnachweis: `.tmp/backend-final-1.8.3.xml`.
Weitere Nachweise im Paketordner: `ct117-report.json`, `ct200-report.json`
und `checks.json`.

Rollback-Snapshots einschließlich Code, Datenbank, Konfiguration und Units:

- Produktion: `/opt/scrapper-code-backups/state-20261004-114218-16100`
- Review: `/opt/scrapper-code-backups/state-20261004-114040-331`

Integrierter Arbeitsstand:
`C:\Users\Entwickler\.codex\worktrees\household-release\Rezepte`.
Der ursprüngliche Checkout bleibt erhalten. Kein Commit, Push oder Branchwechsel.
