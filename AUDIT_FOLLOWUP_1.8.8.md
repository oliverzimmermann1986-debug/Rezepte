# Sechster Prüflauf – Gegenprüfung 1.8.8

Abgeschlossen am 04.10.2026: integrierter Worktree `household-release`,
1.8.8 / Schema 265 auf Review und Produktion mit identischem Paket geprüft.
Originalcheckout mit Schema 232 und fremde Arbeitsstände bleiben erhalten;
den alten Originalcheckout nicht für ein Deployment verwenden.

## Befunde und Korrekturen

| Befund | Gegenprüfung / Änderung |
| --- | --- |
| Schema 232 gegen `origin/main` 260 | Trifft den alten Originalcheckout. Integrierter und zuletzt installierter Code hatten bereits 264, Haushaltsmigration 261 mit Sicherheitsbackup. Neuer Upgradepfad 260 → 265 wird mit dem tatsächlichen lokalen `origin/main`-Code und alten Backups geprüft. |
| Formular-POST mit `Origin: null` | Bestätigt in echtem Chromium: `no-referrer`-Kontrolle führt zu 403. `strict-origin` in Header und Registrierungs-Meta korrigiert Login, Registrierung und Logout; `null` bleibt gesperrt. |
| Login sperrt alle Konten hinter NAT | HTML/native teilen IP plus normalisierten Benutzernamen. Fünf Fehlversuche sperren diesen Zugang; zusätzliche IP-Grenze 100 gegen breit gestreute Versuche. Ein zweites Konto derselben IP bleibt nutzbar. |
| Proxyheader umgehen Sperre | Direkte untrusted TCP-Peers wurden im integrierten Stand bereits ignoriert. Bei konfigurierten Proxys wird die Kette jetzt von rechts bis zum ersten nicht vertrauenswürdigen Peer geprüft; ungültige Ketten fallen auf den TCP-Peer zurück. Betreiber muss sichere Headerweitergabe konfigurieren. |
| Logout-CSRF ohne Cookie | Logout benötigt unabhängig vom Cookie einen passenden Origin. Fremde und fehlende/null Origins erhalten 403 ohne Cookieänderung. |
| SQLite-Locks führen zu 500 | Echte Schreibsperre: vor Antwortbeginn 503, `Retry-After: 1`, allgemeiner Text, `no-store`. Andere SQL-Fehler oder bereits begonnene Streams werden nicht als erneut ausführbarer Request ausgegeben. |
| Retry-Zähler verhindert Absturzerholung | Separater persistenter `recovery_attempts` in Schema 265, auch für Bild-Backfill. Reguläre Retries setzen den Absturzzähler zurück; drei aufeinanderfolgende unerwartete Abbrüche beenden den Auftrag. |
| 18 Verbindungen für Rezeptdetails | Datenabfragen teilen eine schreibgeschützte Verbindung; kein Cache über Anfragen. Auth und Middleware haben weiterhin eigene Abfragen. Tests prüfen Rechtewechsel und 50 parallele Haushaltszugriffe. |
| Papierkorb verliert Quell-URL | Original-URL war in `deleted_url` erhalten; das neue Feld `source_url` blieb leer. Migration 261 ergänzt es bei alten Daten, 265 repariert schon migrierte Bestände, ohne interne private Schlüssel zu veröffentlichen. |
| Locks und frischer Server schreiben unter `/opt` | Locks und Datenbank-Singleton beachten nun die temporäre Config. Ein Konstruktor-Guard im echten Browser-Server lehnt fremde DB-Pfade ab, bevor Datenbank, Verzeichnisse oder Locks geöffnet werden. |

Technische Primärquellen: [Fetch: Request-Origin](https://fetch.spec.whatwg.org/#append-a-request-origin-header),
[W3C: strict-origin](https://www.w3.org/TR/referrer-policy/#referrer-policy-strict-origin).

Umsetzung: `app/security.py`, `app/main.py`, `app/routes/api_auth.py`,
`app/static/register.html`, `app/db.py`, `app/tenancy.py`, `app/jobs/locks.py`
und `app/routes/api_recipes.py`. Neue Regressionen:
`tests/test_audit_load_auth.py`, `tests/test_real_browser_auth.py`;
installierte Gegenprüfung: `tools/probe_load_auth.py`.

## Lokaler Test-Nebeneffekt

Beim ersten neuen echten Browser-Serverlauf wurde der Pfadfehler im
Datenbank-Singleton übersehen. Trotz temporärer Config verwendete dieser Prozess
`C:\opt\scrapper\data\scrapper.db`. Der Lauf ist deshalb kein gültiger Nachweis
für Testisolation. Der Server ist beendet, die Ursache im Code korrigiert und
die Browserprüfungen mit korrektem Pfad erneut ausgeführt.

Die vorhandene lokale DB wurde auf Schema 265 migriert und enthält vier bekannte
synthetische Browser-Testbenutzer. Die Datei bestand bereits vor dieser Runde;
es wurde weder ein Benutzer noch eine Datei gelöscht oder ein ungesicherter
Restore vorgenommen. Die Feststellung zählt nur bekannte synthetische Namen,
ohne personenbezogene Datensätze im Bericht auszulesen.

Nach der Pfadkorrektur erfasster SHA256:
`5887e1614bca66984669cb35068c36213f553b837fc65e72a07b51faf844920e`.
Der endgültige Lauf und das maschinelle Abschlussgate bestätigen denselben Hash.
Evidenz:
`.tmp/browser-server-path-side-effect-1.8.8.json`.

## Abnahme und installierter Stand

- Vollständiger endgültiger Lauf: **928 bestanden, drei Skips, Exit 0**,
  Coverage **64,51 %**. Skips: zwei OCR-Prüfungen mangels Tesseract und der
  Linux-Installer-Test mit root-Anforderung. Keine roten Tests im Abschlusslauf.
- 39 Web- und 32 Expo-Tests bestanden. TypeScript, reguläres Expo-Lint,
  Compileall, Ruff, elf JavaScript-Syntaxprüfungen und `git diff --check` grün.
- Die ersten sechs Fehler betrafen bestehende Testaufbauten: Logout ohne Origin
  und künstliche Alt-DBs, die Migration 261 auslassen. Diese wurden korrigiert.
  Ein weiterer Lauf scheiterte an tatsächlich weniger als 512 MiB freiem
  Windows-Speicher; der Uploadschutz lieferte korrekt 507. Drei eigene,
  abgeschlossene Testverzeichnisse wurden mit NTFS verlustfrei komprimiert,
  ohne Dateien zu löschen. Danach 76 betroffene Tests und der volle Lauf grün.
- Echter temporärer Chromium-Server mit Pfad-Guard: Anmeldung, Registrierung,
  Rotation, Logout/Zurück, Gastrechte, zwei Konten mit Einladung, fremde Origins.
  50 parallele HTTP-Anfragen: **50/50 erfolgreich**, P95 **0,227 s**, 176,09
  Anfragen/s in diesem kleinen synthetischen Windows-Test. Der Datenanteil der
  Detailabfrage öffnet genau eine Verbindung; das ist kein Produktionsbenchmark.
- Paket mit **148 Dateien**, SHA256
  `8559731dfc86e44a44ef1b4faef4c1c1c2567fac0b2952cd2e074a1cd8e5527d`.
  Alle installierten Dateien auf CT 117 und CT 200 stimmen mit dem Manifest
  überein; der lokale gepackte Quellstand ist nach den Tests unverändert.
- Je installiertem Stand alle Lifecycle-Prüfungen (25 plus Isolationsflag), zehn
  Fachlogik-/KI- und zehn neue Auth-/Last-/Upgrade-Prüfungen bestanden. Eigene
  temporäre Daten, keine echten KI-Aufrufe oder Produktionskonto-Erstellung.
- Produktion: **229 globale Rezepte, bestehende Konten und alle 22 geprüften
  Bestandsdatentabellen erhalten**. SQLite/Fremdschlüssel fehlerfrei; keine
  aktiven Imports oder privaten verwaisten Daten. Vorhandener nicht zugeordneter
  Kochfortschritt bleibt erhalten. Kein Papierkorbablauf während des Updates.
- Health und Readiness: **HTTP 200 / ok / 1.8.8**. Webservice, Importtimer und
  Backuptimer aktiv. Review anschließend wieder ausgeschaltet und ausgehängt.
- Automatisches echtes Produktionsbackup vor Migration:
  `/opt/scrapper/data/backups/pre-migration-v264-to-v265-20261004-194524-21432.db`.
  Schema **264**, SQLite `quick_check=ok`, **229 Rezepte** unabhängig geprüft.
- Chromium direkt am Produktivcontainer im LAN: vor Update **403 / Origin null**,
  nach Update **303 / passender Origin**, Gast-Sitzung und Abmeldung erfolgreich.
  Keine echten Zugangsdaten verwendet. Öffentliche Cloudflare-Domain und Google
  Chrome separat nicht geprüft; die lokale Browsersteuerung war nicht verfügbar.

Rückfallstände, jeweils Code, Datenbank und Konfiguration gemeinsam verwenden:

- Review: `/opt/scrapper-code-backups/state-20261004-194148-331`
- Produktion: `/opt/scrapper-code-backups/state-20261004-194441-21018`

Maschinelles Abschlussgate: `.tmp/releases/1.8.8-security/checks.json`.
Backend-XML: `.tmp/backend-final-1.8.8.xml`; installierte Berichte:
`.tmp/releases/1.8.8-security/ct117-report.json` und `ct200-report.json`.
Browserbelege: `.tmp/production-browser-1.8.7.json` und
`.tmp/production-browser-1.8.8.json`.

## Prüfgrenzen

Tests und installierte Probes verwenden synthetische Konten und eigene Pfade,
keine echten KI-/Mail-/Download-Aufträge. Lastmessungen unter Windows sind keine
Produktionskapazitätszusage. Die Anmeldung mit echten Produktionskonten,
öffentliche Cloudflare-Domain, Geräte-GUI, Xcode/App-Store und neue GitHub-CI
sind getrennt zu belegen. Kein Commit oder Push in dieser Runde. Betreiberregeln für Mail,
Selbst-/Supportlöschung und verbleibende Befunde aus `AUDIT_HANDOFF.md` bestehen
weiter. Frühere angefragte oder blockierte Bereinigungen bleiben unangetastet.
Separates Archiv und Supportdienst in dieser Runde nicht ausgerollt; die
Archiver-Code-/Unitdateien sind bytegleich zum unter 1.8.6 geprüften Stand.
