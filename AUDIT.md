# AUDIT — Gesamtliste aller Befunde

<!-- Append-only: Bestehende Einträge werden NIE umgeschrieben oder gelöscht.
     Statusänderungen ausschließlich als Status-Event im Abschnitt „Status-Events".
     Gleicher Root Cause = gleiche ID (auch bei Wiederauftreten); neue ID nur für echtes neues Problem. -->

Evidenzgrade: **[verifiziert]** = Verhalten am Ziel-SHA beobachtet (Test/Repro/Request/echte Engine) · **[statisch-belegt]** = Quelle/Konfiguration belegt eindeutig, Laufzeit nicht ausgeführt · **[unsicher]** = plausible Hypothese mit Fundstelle, Kontrollfluss/Impact offen · **[blockiert]** = Verifikation mangels Plattform/Zugang nicht möglich (Nachholanweisung im Finding).
Herkunfts-Tag: **[Anforderung]** = dokumentierte Produktvorgabe (kombinierbar).

---

## Review-Runden

### Runde 2026-08-27-01

- Ziel: `ce103818967c42291e4cf1030bb1bfd1f3949dd1` · Branch: `main` · Worktree: `clean`
- Modus: `bootstrap` · Dimensionen: `security, stability, performance, operations` · Tiefe: `1 (Scan, rein statisch)`
- Umgebung: Windows-Host, statischer Scan (keine Ausführung, kein Build, kein Netz) — Python/FastAPI-App nicht gestartet
- Baseline: keine (erste Runde)
- Coverage — **geprüft (vollständig):** `app/security.py`, `app/auth.py`, `app/main.py`, `app/config_store.py`, `app/core/{safety,webhook,email_processor,downloader,tiktok_caption}.py`, `app/jobs/{locks,task_queue}.py`, `app/routes/{api_auth,api_users,sharing,api_share,api_config,api_test,api_browse,api_metrics,api_schedule}.py`, alle `.github/workflows/*`, `systemd/*`, `proxmox/*.sh`, `tools/setup_app_review_demo.py`, `requirements.txt`, `pyproject.toml`, `app/static/{runtime.js,sw.js}`, `native-ios/src/lib/{api.ts,auth-context.tsx,external-links.ts}`, README.md, OPTIMIERUNGEN.md
- Coverage — **stichprobenartig:** `app/db.py` (4053 Z., Kernpfade DDL/FTS/Migration/User-Mutationen), `app/routes/{api_recipes,api_admin,api_einkauf}.py`, `app/core/analyzer.py`, `app/jobs/scraper.py` (Subprocess/Playwright), native-ios-Restbaum
- Coverage — **nicht geprüft:** `ios-swift/` (Altbestand, laut Auftrag ausgeschlossen); `video_archiver/`, weitere `tools/`; viele `app/recipes/*` (Domänenlogik) und mehrere `app/routes/*` (api_history, api_events/SSE, api_stats, api_hdd, api_master, api_meal_plan, api_shopping, api_audit) — außerhalb des Scan-Budgets; `node_modules`, `graphify-out`, `__pycache__` (Artefakte)
- Nicht Gegenstand dieser Runde: GUI/UX-Bewertung und Feature-Vorschläge (Dimensionen nicht gewählt)
- Verifikationsgrenzen: Stufe 1 erlaubt keine Ausführung → alle Findings maximal **[statisch-belegt]**; die Laufzeit-Ausnutzbarkeit von B1, C1 ist als **[unsicher]** markiert und in Stufe 2 nachzuweisen (Verifikationsvorschläge je Finding im FIXPLAN)

---

## A — Release-Blocker

— keine in dieser Runde —

## B — Hoch

| ID | Thema | Fundstelle | Fix-Skizze |
|---|---|---|---|
| B1 | **IMAP-Host per Config-API änderbar, während das Passwort maskiert bleibt → Exfiltration der Gmail-App-Passwörter.** Die `SERVER_MANAGED`-Allowlist schützt `ai.openai.base_url` und `external_hdd.shelly_url`, aber NICHT `mail.*.imap_host`; `_unmask` setzt das als `********` gesendete Feld auf das echte, im Klartext gespeicherte App-Passwort zurück. Eine authentifizierte Sitzung kann den Host auf einen Fremdserver zeigen und via `/api/test/mail` oder Scraper ein `LOGIN` mit den echten Zugangsdaten dorthin auslösen. Impact hoch (Credential-Abfluss); Likelihood mittel (Auth/CSRF nötig). Exakt diese Klasse ist für den OpenAI-Key bereits geschlossen — für IMAP fehlt die Kopplung. CWE-522. [statisch-belegt]; Laufzeit-Exfiltration [unsicher] | `app/routes/api_config.py:112` (MASK_PATHS/SERVER_MANAGED), `:288` (`_unmask`); `app/core/email_processor.py:202` (`_connect`→`login`); `app/routes/api_test.py:30` (`/api/test/mail`) @ `ce10381` | `imap_host` (und weitere sicherheitsrelevante Verbindungsziele) in die `SERVER_MANAGED`-Allowlist aufnehmen ODER Passwort-Neueingabe bei Host-Wechsel erzwingen — analog zum bestehenden OpenAI-Base-URL-Muster (`api_config.py:42`, `api_test.py:118`). |

## C — Mittel

| ID | Thema | Fundstelle | Fix-Skizze |
|---|---|---|---|
| C1 | **Open-Redirect: `_safe_next` per Backslash umgehbar.** Die Prüfung lehnt nur Werte ab, die nicht mit `/` beginnen oder mit `//` beginnen; `next=/\evil.com` passiert. WHATWG-Browser normalisieren `\`→`/`, wodurch `Location: /\evil.com` als schema-relatives `//evil.com` gilt → Weiterleitung off-site nach Login (Phishing). Impact mittel; Likelihood browserabhängig. CWE-601. [statisch-belegt]; Browserverhalten [unsicher] | `app/main.py:442` (`_safe_next`), `:558` (`RedirectResponse`) @ `ce10381` | Backslash als Pfadtrenner-Äquivalent behandeln: Werte mit `\` ablehnen bzw. vor der Prüfung normalisieren; nur reine Pfade (kein Schema/Host) zulassen. |
| C2 | **Code sendet Inhalte an OpenAI (US), README/Config/Docstrings behaupten „nur Ollama".** Der reale KI-Pfad ist OpenAI-only (`OPENAI_BASE=api.openai.com`, Ollama-Factory entfernt); die `/privacy`-Seite nennt OpenAI, README und Beispiel-Config widersprechen. Drittlandtransfer USA (DSGVO Art. 44 ff.) für Rezeptbilder/PDFs/Captions. Impact mittel; die vertragliche Grundlage (OpenAI-DPA/SCC) ist **externe Nachweisevidenz**, kein bestätigter Verstoß. Doku-Widerspruch ist belegt. [statisch-belegt] | `app/core/analyzer.py:60`, `:1062` (Factory); `app/main.py:486` (Privacy) vs. `README.md:60`, `:412`; `app/core/downloader.py:1` (veralteter Docstring) @ `ce10381` | README/Beispiel-Config/Docstrings auf OpenAI-Realität nachziehen; DSGVO-Nachweis (DPA + SCC, ggf. DSFA-Prüfung Art. 35) einholen und dokumentieren; falls Ollama beibehalten werden soll, Codepfad wiederherstellen statt nur zu dokumentieren. |
| C3 | **Schwaches Default-Root-Passwort im Container-Provisioning.** `PASSWORD="${PASSWORD:-changeme}"` wird an `pct create --password` gereicht; ohne gesetzte Env erhält der LXC das Trivialpasswort `changeme`. Impact mittel; Likelihood nur bei erreichbarer Konsole/SSH mit Root-Login. Kontrast: App verweigert `admin/changeme`, `install.sh` generiert Zufallspasswörter — das Provisioning bleibt dahinter zurück. CWE-1392/798. [statisch-belegt] | `proxmox/create-container.sh:11`, `:61` @ `ce10381` | Kein Trivial-Default: Zufallspasswort generieren oder Abbruch erzwingen, wenn `PASSWORD` fehlt; Root-Login im Container zusätzlich deaktivieren. |

## D — Klein / Doku / Betrieb

| ID | Thema | Fundstelle |
|---|---|---|
| D1 | **CSP mit `script-src 'unsafe-inline' 'unsafe-eval'` (für Alpine.js) entwertet den XSS-Schutz weitgehend; zusätzlich erlaubt die CSP externe Google-Fonts-Hosts, die das Frontend gar nicht lädt** (widerspricht README „keine externen CDNs"). Kein konkreter Injection-Sink gefunden (Output kodiert). CWE-1021. [statisch-belegt] | `app/security.py:185` @ `ce10381` |
| D2 | **Nicht nach oben begrenzte Abhängigkeit `pymupdf>=1.24.0`** (und weite `pillow`-Spanne) untergräbt die in OPTIMIERUNGEN.md behauptete Reproduzierbarkeit; alle übrigen Laufzeit-Deps sind exakt gepinnt, CI installiert ohne Hash/Frozen-Modus. [statisch-belegt] | `requirements.txt:16`, `.github/workflows/quality.yml:16` @ `ce10381` |
| D3 | **`/metrics` ohne Authentifizierung** (bewusster Trade-off, Kommentar bestätigt Absicht): exponiert Betriebszahlen, falls der Reverse-Proxy den Pfad nicht blockt. Zugangskontrolle liegt allein bei Cloudflare Access/Proxy. CWE-306 (im Kontext abgeschwächt). [statisch-belegt] | `app/routes/api_metrics.py:37` @ `ce10381` |

## F — Feature-Backlog

— nicht erhoben (Dimension `features` in dieser Runde nicht gewählt) —

---

## Positiv bestätigt (geprüft, Behauptung hält)

Für Nachvollziehbarkeit dokumentiert — README-Sicherheitszusagen, die der Code am Ziel-SHA belegt einlöst:

- **Keine SQL-Injection** in geprüften Pfaden: dynamische Sortierung nur über Whitelist-Mappings, Werte durchgängig parametrisiert, FTS5-Tokens gequotet (`app/db.py`, `query_builder`).
- **SSRF-Härtung** stark: HTTPS-Zwang, DNS-Auflösung + IP-Pinning gegen Rebinding, `is_global`-Prüfung, keine Redirects, interne Ziele nur als Literal-IP-Allowlist (`app/core/webhook.py`).
- **Path-Traversal:** alle vier `FileResponse`-Pfade validieren über `resolve_*_under`/`_is_under_temp` inkl. Symlink-Ablehnung (`app/core/safety.py`).
- **Auth/Session:** bcrypt (rounds=12), Session-Versionierung invalidiert Cookies bei Passwort-/Statuswechsel, Default-Login `admin/changeme` beim Start verweigert, Rate-Limiter mit trusted-proxy-XFF, Last-Admin-Schutz, Cookies HttpOnly/SameSite=Lax/secure.
- **`/api/docs` default aus, HSTS bei HTTPS gesetzt.** Share-Tokens HMAC-signiert und widerrufbar. Uploads gestreamt/dekodiert/atomar getauscht. Config-Store atomar, `chmod 0600`, Secrets maskiert.
- **Subprocess/Playwright:** nur Listen-Argumente (kein `shell=True`/`os.system`/`eval`), URLs `https://`-normalisiert bzw. TikTok-validiert.
- **Nebenläufigkeit/Integrität:** Cross-Prozess-File-Locks, Migrations-Lock + Pre-Migration-Backup + Downgrade-Schutz, WAL + `synchronous=FULL`, `BEGIN IMMEDIATE`, Task-Queue-Recovery.
- **systemd-Härtung:** `NoNewPrivileges`, `ProtectSystem=strict`, enge `ReadWritePaths`, Ressourcenlimits, eng begrenzte polkit/sudoers-Regeln.
- **Native iOS-App:** Token im Keychain (`WHEN_UNLOCKED_THIS_DEVICE_ONLY`), Origin-Allowlist, HTTPS-Zwang, `redirect:'manual'`, Session-Epoch gegen stale responses; CI mit `npm ci` und gepinnten Actions.

Hygienehinweis (kein Repo-Finding am Ziel-SHA): `native-ios/credentials.json` enthält lokal ein Klartext-`.p12`-Passwort (Code-Signing), ist aber korrekt gitignored und nie committet (History leer).

---

## Status-Events

<!-- Neuestes zuerst. A-Findings nie ohne dokumentierte Gegenprobe auf „behoben". -->

— noch keine —

### Runde 2026-10-04 — integrierter Haushaltsstand, Kandidat 1.8.5

Der Bericht vom älteren Arbeitsstand wird gegen den integrierten Worktree
`household-release/Rezepte` geprüft. Historische Befunde und Zeilennummern oben
bleiben erhalten. Der aktuelle Arbeitsstand ist nicht committet oder gepusht;
es gibt für ihn keinen bestätigten GitHub-CI-Lauf. Datenbankschema ist 263.
Die früher genannten 8 Fehler bei 524 Tests beschreiben diesen Stand nicht.
Abgeschlossene Nachweise früherer Runden: [1.8.4](AUDIT_FOLLOWUP_1.8.4.md),
[1.8.3](AUDIT_FOLLOWUP_1.8.3.md), [1.8.2](AUDIT_FOLLOWUP_1.8.2.md) und
[1.8.1](AUDIT_FOLLOWUP_1.8.1.md). Der Abschluss dieser Runde wird separat als
Status-Event protokolliert: [1.8.5](AUDIT_FOLLOWUP_1.8.5.md).

#### Kritische Befunde des nachgereichten Berichts

| ID | Befund und aktuelle Evidenz | Status vor Abschluss dieser Runde |
|---|---|---|
| K1 | Private Druck-PDFs waren außerhalb der Haushaltstrennung. `app/tenancy.py`, `app/routes/sharing.py`; Regressionen in `tests/test_tenants.py`, `tests/test_guest_access.py`, `tests/test_pdf_route_compat.py`. `/recipe/*` wird inzwischen ebenfalls geprüft. | Behoben und in früherem Rollout geprüft; Gäste sehen globale Rezepte, keine fremden privaten Rezepte. |
| K2 | Migration 232 erwartete Dict-Zeilen statt Tupeln. `app/tenancy.py` setzt und restauriert die Row-Factory. `tests/test_accounts.py`, `tests/test_tenants.py` und Linux-Rollouts bis Schema 263. | Behoben; bestehende Konten und Daten bleiben erhalten. |
| K3 | IMAP verwendete keinen expliziten verifizierenden SSL-Kontext. `app/core/email_processor.py`, `app/routes/api_test.py`, `tests/test_audit_import_security.py`. Echte lokale TLS-Handshakes prüfen unbekannte CA, falschen Hostnamen und vertrauenswürdigen Host; vor erfolgreicher Zertifikatsprüfung wird kein LOGIN gesendet. | In 1.8.5 korrigiert; endgültiger Gesamtlauf/Rollout noch ausstehend. |
| K4 | Punktpfade aus KI-/manuellen Metadaten konnten Speicherwurzeln verlassen. Gemeinsamer `safe_path_component` in `app/core/safety.py`, verwendet in `app/jobs/scraper.py` und `app/recipes/manage.py`; Punktpfade, Gerätenamen und UTF-8-Länge regressionsgeprüft. | In 1.8.5 korrigiert; zusätzlich echter Speichervorgang auf Linux vorgesehen. |

#### Hohe Befunde

| ID | Befund | Aktueller Stand |
|---|---|---|
| H1 | Automatischer Mailimport ohne freigegebene, überprüfte Absenderidentität. | Offen: erlaubte Adressen sind beim Betreiber angefragt. Die bestehende Mailkonfiguration wurde nicht verändert. Eine einfache `From`-Allowlist allein schützt nicht gegen gefälschte Header; Provider-/Authentifizierungsnachweis ist Teil der Umsetzung. |
| H2 | Große Bodies außerhalb von Upload-APIs, auch ohne Login. | 1.8.5 begrenzt alle Content-Types vor Parsing: regulär 1 MiB; Datei-/Fotoimporte haben definierte höhere Grenzen. Tests prüfen bekannte und unbekannte Länge. |
| H3 | Offene Registrierung/Gastzugang ohne Importkontingent. | Gäste sind lesend und rate-limitiert. Persistente Haushalts-/Serverkontingente begrenzen kostenpflichtige Importe einschließlich Bildanalysen; aktive Wiederholungen werden dedupliziert. Frühere Runden und `tests/test_import_budget.py`, `tests/test_guest_access.py`. |
| H4 | Selbstständige Kontolöschung fehlt. | Offen. Administratives Löschen ist gegen verwaiste Haushaltsdaten gehärtet; es ersetzt keine vollständige Selbstlöschung. Die Behauptung eines sicheren App-Store-/DSGVO-Verstoßes ist hier nicht als Rechtsbefund bestätigt. |
| H5 | Unfertige Expo-Rezepte zeigten „Kochfertig“. | Fünf Zustände über `native-ios/src/lib/recipe-status.ts` und Webstatus; Node-Regressionen. Quellkorrektur vorhanden, kein neuer nativer Build in dieser Runde. |
| H6 | Webstatusfarben unter AA-Kontrast. | Gemeinsame hellen Statusfarben korrigiert; rechnerische Kontrasttests. Ein vollständiger Geräte-/Screenshot-Audit bleibt gesondert. |
| H7 | Kleine Touch-Ziele im Web/Expo. | Zentrale Bedienelemente auf mindestens 44 px angepasst, Quell-/GUI-Prüfungen früherer Runde. Vollständige Geräteprüfung ausstehend. |

#### Mittlere Befunde und GUI-Konsistenz

| ID | Befund | Aktueller Stand |
|---|---|---|
| M1 | Fremde HTML-Seite beansprucht bekannte Canonical-URL. | 1.8.5 erlaubt nur den Origin der tatsächlich abgerufenen, validierten Seite. DNS-Pinning und Redirectprüfungen bleiben aktiv; TikTok-Kurzlinks behalten ihren separaten Auflösungspfad. |
| M2 | Überdimensionale PDF-Vorschau. | 1.8.5 setzt `pdftoppm -scale-to 1600` vor der Rasterung; Test plus echter Linux-Render einer synthetischen 20000×20000-Punkt-Seite vorgesehen. Das begrenzt diesen Vorschaupfad, nicht pauschal sämtliche PDF-Verarbeitungen. |
| M3 | Thumbnail-Verarbeitung blockiert den Web-Eventloop. | In 1.8.4 auf Worker ausgelagert und mit dateibasierten Prozesslocks/atomarer Veröffentlichung gegen Nebenläufigkeit gehärtet. |
| M4 | Video-Downloads ohne Größenlimit. | 1.8.5: Vorabgrenze für bekannte Größen, Überwachung von Dateien/Fehlerlog bei unbekannten Streams, Timeout, Prozessgruppen-Abbruch auf Linux und Bereinigung nur des eigenen Ordners. 100 MiB ist eine Abbruchschwelle, kein bytegenaues Hard-Limit zwischen Messintervallen. |
| M5 | Fremde Freigabelinks sichtbar/widerrufbar. | Stabile Haushaltsbesitzer-ID und gefilterte Abfragen; fremder Widerruf abgelehnt. Tests plus synthetische Linux-Probe früherer Runde. |
| M6 | Gäste sehen Importjob-SSE. | `/api/events` und Verwaltungsdaten erfordern Admin; Gast-Negativtests und Liveprüfung. |
| M7 | Fehlende Identität fällt auf Admin zurück. | Kein Admin-Fallback; nicht erkannte Identität wird abgelehnt. Auth-/Haushaltstests. |
| M8 | Einladungstoken in URL/Access-Log. | Offen für vollständige Prüfung aller Erzeuger, Web-/nativen Empfänger und Proxylogs. Gehashte Speicherung und zeitliche Gültigkeit reduzieren das Risiko, beseitigen URL-Leaks nicht. |
| M9 | Vertippte Zugangsdaten in fehlgeschlagenen Loginlogs. | Frühere Runde entfernt eingegebenen Benutzernamen aus dem Fehlerlog; weiterhin nötige IP-basierte Missbrauchsbegrenzung. Keine pauschale Datenschutzbewertung. |
| M10 | Backup-Button ruft fehlende Methode auf. | 1.8.5 ergänzt `runBackupNow`; eine POST-Anfrage, Wartungsansicht aktualisiert, Fehler angezeigt und Button wieder freigegeben. Zwei Webregressionen. |
| M11 | Service-Worker-Cache wächst/offline funktioniert nie. | Alter Bericht teilweise widerlegt: Aktivierung löscht alte Cacheversionen, öffentliche Assets haben Offline-Fallback. Login, Navigation, APIs und private Medien bleiben aus Sicherheitsgründen netzgebunden. `no-store` bei statischen Fetches bleibt eine offene Leistungsoptimierung; private Offline-API-Caches werden nicht eingeführt. |
| M12 | Einladung lässt Haushalt ohne Rückfrage beitreten. | Ein Link füllte im geprüften Stand nur das Formular vor. 1.8.5 verlangt zusätzlich ausdrückliche Bestätigung vor Datenübernahme und POST; Abbruch sendet nichts. |
| M13 | Doppeltes Absenden erzeugt Einkaufsduplikate. | 1.8.5 serialisiert Webübermittlungen einschließlich externer Listen; Buttons gesperrt, Fehler geben sie frei. Drei Webregressionen. Das ist kein datenbankweites Verbot identischer Artikel. |
| M14 | Expo-Kochtimer meldet im Hintergrund/bei Displaysperre nicht. | Offen; echte Geräteprüfung und native Benachrichtigungen erforderlich. Keine solche Geräte-/Releaseprüfung in dieser Runde. |
| M15 | Private Expo-Bilder bleiben nach Sessionablauf gecacht. | Zusätzlich bestätigt: 401-Pfade löschten bisher nur den API-Cache. 1.8.5 räumt nun auch Speicher-/Festplattenbilder beim Start, allgemeinen 401 und Fokuscheck auf; ein Cachefehler verhindert die andere Bereinigung nicht. Netzfehler erhalten die Sitzung. Node-Verifikation, Gerätetest ausstehend. |
| M16 | Expo-Start wartet bei unerreichbarem Server 20 Sekunden. | Sitzungsprüfung beim Start in 1.8.5 auf 5 Sekunden begrenzt. TypeScript/ESLint geprüft; keine Messung eines installierten App-Binaries. |
| M17 | Farben/Eckenradien unterscheiden sich zwischen Clients. | Zentrale Tokens teilweise vereinheitlicht. Restliche visuelle Prüfung offen; `ios-swift` ist inzwischen der primäre native Client, die historische Altbestand-Einordnung gilt nicht mehr. |
| M18 | Unterschiedliche Titeltypografie. | Offen für visuelle Abnahme Web/primärer Swift-App; kein grundloser Komplettumbau des älteren Expo-Clients. |
| M19 | Einkaufsliste/Einkauf/Einkaufskorb uneinheitlich. | Teilweise vereinheitlicht; restliche Texte in allen Clients prüfen. |
| M20 | Englische Resttexte/rohe Jobstatuswerte. | Teilweise korrigiert; vollständige Textinventur bleibt offen. |

#### Status-Events zu historischen IDs

| ID | Status-Event 2026-10-04 |
|---|---|
| B1 | Geschützte Mail-Verbindungsziele in `app/routes/api_config.py`; Zieländerung mit maskiertem Passwort wird nicht als beliebiger Credential-Sendeweg akzeptiert. Vollständiger neuer Mailserver-Test ohne echte Zugangsdaten. |
| C1 | `_safe_next` verwirft Backslashes und externe Redirectziele; Loginregressionen. |
| C2 | README-Ollama-Beispiel in 1.8.5 auf den vorhandenen OpenAI-Pfad korrigiert; Config und Privacy nennen OpenAI. Externe Vertrags-/Datenschutznachweise bleiben Betreiberaufgabe und wurden nicht überprüft. |
| C3 | Container-Provisioning verweigert fehlendes/`changeme`-Rootpasswort. Keine neuen realen Zugangsdaten erstellt. |
| D1 | Externe Schrift-CDNs und `unsafe-inline` entfernt; `unsafe-eval` für vorhandenes Alpine bleibt offen. Kein neu nachgewiesener XSS-Sink. |
| D2 | PyMuPDF/Pillow sind exakt gepinnt; vollständige Dependency-Lock-/Hashinstallation ist damit noch nicht nachgewiesen. |
| D3 | `/metrics` ist Admin-geschützt; Gast-/Anonymzugang verweigert. |

Die positiven Aussagen des früheren Berichts gelten nur in den dort geprüften
Pfaden. Aktuelle Tests prüfen Haushaltsgrenzen, SSRF/Dateiwurzeln, private
Cacheheader und RBAC. Kein pauschaler Beweis für XSS-Freiheit, alle PDF-Decoder,
App-Store-Abnahme oder Rechtskonformität. Tests erzeugen keine realen Konten,
Einladungen, Mailabrufe oder kostenpflichtigen KI-Aufträge.

### Ergänzung der Linux-Gegenprobe 2026-10-04

| ID | Neuer Befund und Gegenprobe | Korrektur |
|---|---|---|
| M21 | Review hatte kein `pdftoppm`; der Python-Vorschaupfad brach mit `FileNotFoundError` ab. Produktion hatte das Werkzeug bereits. | Installation und lokales Update installieren das benötigte `poppler-utils`. Bestehende Timer bleiben erhalten. 30 Installations-/Release-Guardtests bestanden. |
| M22 | Poppler hängt `.jpg` an den vollständigen Ausgabeprefix an. `prefix.with_suffix('.jpg')` entfernte dagegen die Thread-ID; vorhandene Vorschauen wurden nicht gefunden und temporäre Bilder blieben zurück. Die echte Linux-Probe und anschließend die auf Popplers tatsächlichen Namen korrigierte Regression scheiterten vor dem Fix. | `ensure_pdf_first_page` verwendet nun `prefix.name + '.jpg'`; vorhandene Vorschau wird atomar übernommen, Entwurf im Fehlerpfad entfernt. 45 PDF-/Bild-/Versionsfälle bestanden; neuer Gesamtlauf und echter Linux-Render folgen. |

Der alte Mock war kein Nachweis des echten Poppler-Dateinamens. Sein Fehler
wurde korrigiert; ein vollständiger Backendlauf vor M22 gilt nicht als endgültiges
Gate für den danach geänderten Backendstand.

### Status-Event — Abschluss 1.8.5 am 2026-10-04

- **K3, K4, H2, M1, M2, M4, M10, M12, M13, M21, M22:** korrigiert,
  regressionsgeprüft und als identisches Backend/Web-Paket zuerst auf Review,
  danach auf Produktion verifiziert. Echte Linux-Proben bestätigen begrenzten
  PDF-Render, sicheren Speichervorgang und Downloadabbruch mit Kindprozessen.
- **K1, K2 und die Haushaltskorrekturen früherer Runden:** Regressionen bleiben
  grün; globale Gast-PDFs/acht Rechteverbote und Schema 263 am Dienst geprüft.
- **M15, M16:** Expo-Quellkorrektur mit 27 Node-Tests, TypeScript und ESLint
  geprüft; kein neuer nativer Build/keine Geräteabnahme. GUI-Quellkorrekturen
  ersetzen die noch offene visuelle Abnahme nicht.
- **C2-Dokumentation:** korrigiertes OpenAI-Beispiel ist im Paket enthalten;
  externe Betreiber-/Datenschutznachweise bleiben offen.
- Endgültig **819 Backendtests bestanden, 2 OCR-Tests übersprungen**, Coverage
  **63,52 %**; **39 Webtests**, **27 Expo-Tests** und Codeprüfungen bestanden.
- Produktion: **229 aktive Rezepte**, ursprüngliche Werte in **22 Tabellen**
  und bestehende Konten erhalten; **132 Paketdateien** manifestgeprüft.
  Health/Readiness 200/1.8.5, Dienst und beide bisherigen Timer aktiv.
  Review ausgeschaltet und ausgehängt. Keine echten KI-/Mailimporte durch Tests.
- **Offen:** H1, H4, M8, M11-Leistungsoptimierung, M14, restliche GUI-/Texte-
  Abnahme und D1 `unsafe-eval`. Kein GitHub-CI-Lauf, Commit, Push oder App-Release
  für diesen uncommittierten Stand behauptet.

Vollständiger Nachweis einschließlich Paket-SHA und Rückfallpfaden:
[AUDIT_FOLLOWUP_1.8.5.md](AUDIT_FOLLOWUP_1.8.5.md).

### Vierter Prüflauf – Korrekturstand 1.8.6 vom 04.10.2026

Die historischen Befunde bleiben erhalten. Die nachgereichten Aussagen wurden
am integrierten Stand neu geprüft; alte Token-, Kosten- und Fehlerzahlen sind
kein aktueller Nachweis.

| ID | Priorität / Befund | Umsetzung oder verbleibende Grenze |
|---|---|---|
| F4-H1 | Hoch: umgezogene Archiver-venv | Permanente venv und Symlink; echter Linux-Launcher-/Update-/Rollbacktest. Der aktuelle Produktionslauncher war bereits repariert. |
| F4-H2 | Hoch: Jobfehler als Installationsgate | Installer startet keinen Download. Unit akzeptiert behandelte rc=1; Infrastrukturfehler bleiben Fehler. |
| F4-H3 | Hoch: Thumbnail-no-store | Authentifizierter privater ETag-Cache; 200/304, fremde 404 und anonyme 401 getestet. Alle anderen privaten Antworten bleiben no-store. |
| F4-H4 | Hoch: Auth blockiert Eventloop / doppelte Abfrage | Worker und requestlokaler Snapshot; offene SSE-Verbindungen verwerfen den Snapshot vor erneuter Prüfung. Thread-/Abfrage-/Widerrufstest. |
| F4-H5 | Hoch: Expo-Kochmodus | Fokusgebundenes Keep-Awake ergänzt. Fehlende Schritte wurden im Load-Pfad bereits abgefangen; zusätzliche defensive Anzeige, kein bestätigter alter Absturz. Gerätetest offen. |
| F4-H6 | Hoch: Expo-VoiceOver | Bewertung, Zustand, semantische Überschriften und Dynamic-Type-Text ergänzt. Statuslabel war bereits vorhanden. TSX-Harness; Geräteabnahme offen. |
| F4-M1 | Mittel: Cart-Read nimmt Schreibsperre | Lesender Fastpath ohne fällige Regel; tatsächliche Mutation bleibt im Haushalts-/SQLite-Guard. Kontentionstest. |
| F4-M2 | Mittel: FTS-OR-Teilwortsuche | Einmalige Kandidaten-IDs; identische Resultate bei 2000 Rezepten. Fünf von sechs Messungen schneller; Teilwortscans bleiben. |
| F4-M3 | Mittel: Timeout / temporäre Reste | 3900 s für 4x900 s plus Reserve; SIGTERM-Probe stoppt Downloader, räumt auf und requeued. SIGKILL/Stromausfall bleiben eine Grenze. |
| F4-M4 | Mittel: Restore bei aktiven Timern | Prüft Job- und Backup-Timer; Ablehnung vor DB-Schreibzugriff getestet. |
| F4-M5 | Mittel: Supportaufbewahrung / Löschung | Offen: separater Dienst CT 118, Privacy bietet Löschung auf Anfrage mit Referenz. Keine Frist behaupten oder echte Tickets ohne Betreiberregel löschen. Aktueller separater Checkout und Bearbeitungsprozess zu klären. |

Browserprofil weiterhin vorhanden: automatische Löschung mit „blocked by policy“
abgelehnt, ausdrückliche Bestätigung angefragt. `.codex-tmp*/` ist ignoriert.
Prüfdetails, Messgrenzen und Rollout: [AUDIT_FOLLOWUP_1.8.6.md](AUDIT_FOLLOWUP_1.8.6.md).
Priorisierte Gesamtübergabe: [AUDIT_HANDOFF.md](AUDIT_HANDOFF.md).

#### Abschluss 1.8.6 – endgültiger Stand vom 04.10.2026

- F4-H1–H4 und F4-M1–M4 korrigiert und auf Review/Produktion geprüft;
  F4-H5/H6 in der Expo-Quelle korrigiert, Geräteabnahme offen.
- 830 Backendtests bestanden, drei lokale Skips; Coverage 63,89 %.
  39 Web- und 32 Expo-Tests sowie TypeScript/ESLint bestanden.
- Endgültiges Paket: 140 Dateien, gleicher Hash auf beiden Containern.
  15 Linux-Installer-/Rollback-/SIGTERMprüfungen bestanden. Updates bewahren
  auch angepasste Workerargumente und Timerzyklen; größere Jobmengen erhalten
  ein größeres Budget. Kein neuer GitHub-CI- oder nativer Buildnachweis.
- Produktion: 229 Rezepte, Konten und 22 Tabellen erhalten, Health/Readiness 200,
  Webservice und beide App-Timer aktiv. Review ausgeschaltet und ausgehängt.
- Separates Archiv: Queue und 215 Dateien erhalten; bestehender Ein-Job-Worker
  und Timer unverändert. Launcher/geladenes Zeitbudget/Exit-Policy geprüft.
  Frühere fehlgeschlagene Aufträge wurden nicht als echte Downloads wiederholt.
- Offen bleiben die Betreiberregeln für H1/H4/F4-M5, M8/M10, M11, M14,
  restliche Geräte-/GUI-Abnahme und C2/D1. Browserprofil nicht gelöscht,
  automatische Löschfreigabe war blockiert; ausdrückliche Antwort steht aus.

Nachweis: [AUDIT_FOLLOWUP_1.8.6.md](AUDIT_FOLLOWUP_1.8.6.md).
Priorisierte Übergabe: [AUDIT_HANDOFF.md](AUDIT_HANDOFF.md).

### Fünfter Lauf – Gegenprüfung und Umsetzung für 1.8.7

Auth-String, PDF-Mengen, KI-Belege/Abbruch, Suche, gekaufte Einkaufspositionen,
Testpfade, serverweite KI-Kontingente, Quellcover, Signaturhinweise und öffentliche
Fehlertexte werden in [AUDIT_FOLLOWUP_1.8.7.md](AUDIT_FOLLOWUP_1.8.7.md) einzeln
abgeglichen. Die pauschale Aussage zu fehlenden Sicherheitsregressionen ist für
den integrierten 1.8.6-Stand widerlegt. Bruchmengen beim Kochen werden nicht blind
aufgerundet. `C:\opt\scrapper` enthält ältere Daten; nur fünf heutige Testdateien
sind zur Löschung angefragt. Der Abschlusslauf und der Rollout werden nach
Bestätigung ihrer tatsächlichen Ergebnisse ergänzt.

#### Abschluss 1.8.7 – endgültiger Stand vom 04.10.2026

- Bestätigte Befunde des fünften Laufs korrigiert; zwei beim Gesamtlauf
  aufgefallene Wochenplanregressionen behoben, gepflegte Mappings und
  Ausschlüsse erhalten. Erneuter vollständiger Lauf: 907 bestanden, drei
  Skips, Exit 0, Coverage 64,34 %. 39 Web- und 32 Expo-Tests sowie lokale
  Typ-/Syntax-/Lintprüfungen bestanden.
- Identisches Paket mit 146 Dateien auf Review und Produktion geprüft,
  Version 1.8.7 / Schema 264. Je Umgebung zehn neue Fachlogik-/KI- und
  25 Lifecycle-/Sicherheitsprüfungen mit temporären Daten bestanden.
- Produktion: 229 Rezepte, bestehende Konten und alle 22 Bestandsdatentabellen
  erhalten. Health/Readiness 200; Webservice und beide Timer aktiv. Review
  ausgeschaltet und ausgehängt. Keine echten KI-Aufrufe durch die Prüfungen.
- Kein neuer Archiver-/Support-Rollout; Archiver-Dateien bytegleich zum
  geprüften 1.8.6-Stand. Kein Commit, Push, aktueller CI- oder nativer Buildnachweis.
- Betreiberregeln, native Geräteabnahme und übrige Restbefunde bleiben in
  der [Übergabe](AUDIT_HANDOFF.md). Bruchmengen nicht still aufgerundet;
  KI-Zähler sind keine monetäre Obergrenze. Angefragte bzw. automatisch
  blockierte Bereinigungen nicht durchgeführt.

Paket-SHA, Rückfallstände und Abnahmegrenzen:
[AUDIT_FOLLOWUP_1.8.7.md](AUDIT_FOLLOWUP_1.8.7.md).

### Sechster Lauf – Gegenprüfung und Abschluss 1.8.8

Die Schema-232-Warnung betrifft den alten Originalcheckout. Integrierter und
zuvor installierter Stand hatten bereits 264; jetzt 265. Der Upgradepfad vom
lokalen `origin/main` 260 sowie künstliche 231/232-Bestände ist mit Datenerhalt,
Sicherheitsbackup, Fehler-Rollback und Restore für alten Code geprüft.

- Formular-Origin-Fehler auch am echten Produktivcontainer in Chromium bestätigt:
  vor Update Gastformular 403 / Origin null, danach 303 / passender Origin.
  Header und Registrierungs-Meta korrigiert; Gast-Sitzung und Logout erfolgreich.
  Kein reales Konto benutzt; öffentliche Cloudflare-Domain separat offen.
- NAT-/Proxy-Zähler, Logout-CSRF, SQLite-503, unabhängige Absturzerholung,
  Detail-Leseverbindung, Papierkorbquellen und Config-/Lockpfade korrigiert.
  50/50 synthetische parallele HTTP-Reads erfolgreich, P95 0,227 s unter Windows.
- Vollständiger Abschlusslauf 928 bestanden, drei Skips, Exit 0, Coverage 64,51 %.
  39 Web- und 32 Expo-Tests sowie Typ-/Syntax-/Lintprüfungen grün. Die vorherigen
  Logout-/Alt-DB-Testaufbauten korrigiert; die spätere Speicherknappheit ohne
  Dateilöschung durch NTFS-Komprimierung eigener abgeschlossener Artefakte behoben.
- Identisches Paket mit 148 Dateien auf Review und Produktion verifiziert,
  1.8.8 / Schema 265. Alle Lifecycle-, Fachlogik-/KI- und neuen Auth-/Last-Probes
  mit temporären Daten bestanden. Kein Commit, Push, neuer CI- oder Gerätebuild.
- 229 Rezepte, bestehende Konten und 22 Bestandsdatentabellen erhalten. Echtes
  automatisches Backup 264 mit 229 Rezepten und SQLite `ok` geprüft; Rückfallstände
  bewahrt. Health/Readiness 200, Service und Timer aktiv, Review ausgeschaltet
  und ausgehängt. Separates Archiv und Support in dieser Runde nicht ausgerollt.
- Eigener Testfehler: erster neuer Browser-Server nutzte trotz temporärer Config
  die lokale DB `C:\opt\scrapper\data\scrapper.db`. Schema 265 und vier bekannte
  synthetische Benutzer dort dokumentiert; keine Bereinigung. Seit Pfadkorrektur
  Hash unverändert. Browser-Server lehnen jetzt fremde DB-Pfade vor Zugriff ab.

Einzelbefunde, Grenzen, Paket-SHA und Rückfallstände:
[AUDIT_FOLLOWUP_1.8.8.md](AUDIT_FOLLOWUP_1.8.8.md).
Offene Betreiber-/Geräte-/CI-/Web-Themen: [AUDIT_HANDOFF.md](AUDIT_HANDOFF.md).
