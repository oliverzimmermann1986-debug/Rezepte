# Audit-Nacharbeit 1.8.2

Historischer Release-Nachweis. Die weitere Nacharbeit steht in
[`AUDIT_FOLLOWUP_1.8.3.md`](AUDIT_FOLLOWUP_1.8.3.md).

Stand: 04.10.2026. Backend und Web laufen auf Produktion CT200 mit **1.8.2**
und Datenbankschema **262**. Derselbe Paketstand wurde zuerst auf Review CT117
geprüft. Review ist wieder ausgeschaltet und nicht eingehängt.

## Korrekturen und zusätzliche Gegenprüfungen

1. **F1 behoben — wiedervergebene Namen übernehmen keine Freigaben.** Neue
   Freigaben speichern eine stabile Haushaltskennung. Beim Beitritt ziehen
   sie mit; nach dem Löschen des Erstellers verwaltet die verbleibende Person
   sie weiter. Eine erneute Registrierung des Namens bekommt weder die Liste
   noch Widerrufsrechte. Die Regression prüft diesen Ablauf über echte
   HTTP-Sitzungen. Quellen: `app/tenant_db.py:31`, `app/tenant_db.py:36`,
   `app/tenancy.py:185`, `app/tenancy.py:214`.
2. **F2 behoben — Importe können nicht in einen gelöschten Haushalt schreiben.**
   Haushaltsänderungen und Beitritt verwenden dieselbe Betriebssystem-Sperre;
   vor dem Schreiben wird die Mitgliedschaft erneut geprüft. Das gilt auch
   für Dateiimporte, Fotoanalysen, Pending-Aktionen und Einkaufsänderungen.
   Überschneidungen liefern 409. Persistierte laufende Importe verhindern den
   Beitritt weiter, bis sie abgeschlossen sind. Tests erzwingen beide
   Reihenfolgen sowie eine Sperre aus einem separaten Prozess. Quelle:
   `app/tenancy.py:33`, `app/accounts.py:133`.
3. **F3 stabilisiert — letzter Besitzer bleibt bei vorhandenen Daten erhalten.**
   Eine administrative Löschung mit privaten Rezepten, Importen, Sammlung,
   Einkauf oder Planung wird atomar mit 409 abgewiesen. Ein leerer Haushalt
   bleibt löschbar; bei zwei Personen funktioniert die Eigentumsübergabe.
   Die eigentliche Kontoselbstlöschung und geregelte Datenbereinigung sind
   weiterhin offen. Quelle: `app/db.py:4340`.
4. **F4 behoben für Nutzerimporte — persistente Kontingente vor der Analyse.**
   Standardmäßig gelten 20 Analysen pro Haushalt und 200 auf dem Server über
   gleitende 24 Stunden. Die Werte sind im YAML konfigurierbar. Die SQLite-
   Transaktion schützt die Gesamtgrenze über Prozesse und Neustarts hinweg.
   Globale Verknüpfungen, Upload-Replays und aktive URL-Duplikate bleiben frei.
   Link-, Datei-, Foto-, Wiederholungsanalysen und Share-Intakes sind erfasst;
   Mail-/CLI- und gesonderte Admin-Batchjobs verwenden diese Grenze nicht.
   Gastanmeldungen sind gemeinsam für API und Browser begrenzt. Quellen:
   `app/import_budget.py:21`, `app/routes/api_auth.py:27`.
5. **Zusätzlich gefunden und behoben — globale Importe wurden privat eingereiht.**
   Die Haushaltsfassade ergänzte bei fehlendem `account_id` den Haushalt des
   Absenders. Globale Aufträge übergeben jetzt ausdrücklich `None`. Regressionen
   prüfen die persistierte Queue, den Worker und den Abruf durch einen anderen
   Haushalt sowie das Share-Token eines zusätzlich angemeldeten Absenders.
   Quelle: `app/routes/api_pending.py:344`, `app/routes/api_share.py`.

Schema 262 übernimmt historische Freigaben nur bei nachvollziehbarem Besitzer.
Fehlende oder erst später neu angelegte Ersteller erhalten keine Rechte an
alten globalen Freigaben. Unzugeordnete Links behalten Token, Laufzeit und
Widerrufsstatus. Auf Produktion gab es vor der Migration keine Freigaben oder
privaten Rezepte; dort musste kein unklarer Eigentümer rekonstruiert werden.

## Prüfungen

- Vollständiger isolierter Backendlauf: **774 bestanden, 2 übersprungen** in
  275,42 s. Beide übersprungenen OCR-Tests benötigen lokal fehlendes Tesseract.
- Web-Tests: **32 bestanden**; Expo-Tests: **25 bestanden**.
- Review und Produktion: Migration, SQLite-Integrität, Fremdschlüssel,
  Anmeldung, Gastzugang, acht Schreib-/Adminverbote und Druck-PDF erfolgreich.
- Auf beiden Linux-Containern zusätzlich mit einer temporären synthetischen
  Datenbank geprüft: Namenswiedervergabe, verbleibender Freigabebesitzer,
  Löschschutz, Sperre zwischen zwei Prozessen, private Datenübernahme und
  persistentes Importlimit. Keine echten Konten oder Einladungen dafür erzeugt.
- Alle **129 installierten Paketdateien** stimmen mit dem Manifest überein.
  Produktion erhält alle **229 aktiven globalen Rezepte** und die vorhandenen
  Werte in **20 geprüften Tabellen**, einschließlich Konten, Mitgliedschaften,
  Einladungen, Einkauf, Planung und Freigaben. Keine Datenbereinigung oder
  Änderung bestehender Zugangsdaten durchgeführt.
- Produktionsdienst und beide vorhandenen Timer sind aktiv; Bereitschaft und
  Gesundheitsprüfung liefern 200 mit Version 1.8.2. Keine aktiven Importjobs
  oder verwaisten privaten Rezepte in der abschließenden Prüfung.

Keine Änderung am nativen Client in dieser Runde. Xcode, XCTest, Simulator,
IPA-Erstellung und TestFlight wurden hier nicht ausgeführt. Die bestehende
CSP mit `unsafe-eval` bleibt ein offener Punkt aus dem früheren Audit.

## Release-Nachweise

Paket: `.tmp/releases/1.8.2-security/rezepte-1.8.2.tar.gz`.
SHA-256: `6ee6aa10d9464b4bfe53ffdc52264c485e218fcd9830dedaa215b34c4361dc7b`.
Lokal gesichert: `ct117-report.json`, `ct200-report.json`,
`checks.json` im selben Ordner und `.tmp/backend-final-1.8.2.xml`.

Rollback-Snapshots einschließlich Code, Datenbank, Konfiguration und Units:

- Produktion: `/opt/scrapper-code-backups/state-20261004-102555-15490`
- Review: `/opt/scrapper-code-backups/state-20261004-102417-334`

Integrierter Arbeitsstand:
`C:\Users\Entwickler\.codex\worktrees\household-release\Rezepte`.
Der ursprüngliche Checkout bleibt erhalten. Kein Commit, Push oder Branchwechsel.
