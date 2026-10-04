# FIXPLAN — Umsetzungsplan

<!-- Abgeleitete Planungssicht aus AUDIT.md — darf aktualisiert werden.
     Keine Finding-Beschreibungen duplizieren; AUDIT.md bleibt die Befundquelle. -->

Aktueller Stand: 2026-10-04 · Quelle: [AUDIT.md](AUDIT.md), Runde 2026-10-04.
Die Pakete von 2026-08-27 unten sind historische Planung; maßgeblich ist dieser
aktuelle Abschnitt. Letzter Abschluss: [1.8.8](AUDIT_FOLLOWUP_1.8.8.md),
priorisierte [Übergabe](AUDIT_HANDOFF.md). 928 Backend-, 39 Web- und 32 Expo-Tests
bestanden; drei lokale Skips, Coverage 64,51 %. Paket 1.8.8 mit 148 Dateien auf
Review und Produktion geprüft; 229 Rezepte, Konten und 22 Tabellen erhalten.
Archiver-Quelle bytegleich mit dem unter 1.8.6 geprüften Stand; kein neuer Rollout.

## Aktuelle Umsetzung und Restarbeiten

- [x] **Sechster Lauf / 1.8.8:** Formular-Origin, Logout-CSRF, NAT-/Proxy-Limits,
  SQLite-503, unabhängige Worker-Absturzzähler, eine Detail-Leseverbindung,
  Papierkorbquellen und Config-/Lockpfade korrigiert. Alt-Upgrade 260 → 265
  inklusive Backup/Restore geprüft; identischer Hash auf Review/Produktion,
  echtes Produktionsbackup 264 erhalten und geprüft, Chromium-Gastzugang im LAN
  erfolgreich. Eigener lokaler `C:\opt`-Testfehler dokumentiert; keine Bereinigung.
  Öffentliche Domain, Geräte und CI bleiben separate Abnahmegrenzen.
- [x] **F5-H1–H6:** Auth-Boolean, direkte Testpfadisolation, KI-Belege/
  Abbruch/Confidence, PDF-Mengen, Ausschlüsse/Phrasen, neu benötigte Cart-Mengen.
- [x] **F5-M1–M7, Quelle:** bekannte Katalogfehler, Zubereitungszusätze mit
  erhaltenen eigenen Mappings, ml/l, Quellcover/Detailkennzeichnung,
  KI-Versuchskontingente, Mail-Hinweise/Datenschutztext, allgemeine Serverfehler.
- [x] **1.8.7-Abschluss:** endgültiger vollständiger Testlauf, Review/Produktion
  mit identischem Paket, Erhalt der Daten und Timer. Siehe
  [Gegenprüfung](AUDIT_FOLLOWUP_1.8.7.md). Geräte- und Betreiberabnahme bleiben offen;
  Kontingente ersetzen keine monetäre KI-Grenze oder vollständige Quellbelegprüfung.

- [x] **K1, K2, H3, M3, M5–M7, M9:** Haushalts-/PDF-Isolation, Migration,
  Gastrechte, persistente Importbudgets, Upload-Nebenläufigkeit, Freigabelinks
  und fehlende Identität in vorangegangenen integrierten Runden korrigiert.
- [x] **K3, K4, H2, M1, M2, M4:** Mail-TLS, gemeinsame Pfadbereinigung,
  Bodylimits, Canonical-Origin und PDF-/Downloadgrenzen in Kandidat 1.8.5
  umgesetzt. Abschlussgate: vollständige Suite, Linux-Review, identisches
  Paket auf Produktion, Daten-/Timer-/Manifestkontrolle.
- [x] **M10, M12, M13:** Backup-Aktion, Beitrittsbestätigung und Schutz gegen
  doppelte Einkaufsübermittlung mit Webregressionen umgesetzt.
- [x] **M15, M16:** Expo-Cachebereinigung bei Sitzungsablauf und kürzere
  Startprüfung im Quellstand umgesetzt; nativer Gerätetest bleibt offen.
- [x] **C2-Dokumentation:** README zeigt den vorhandenen OpenAI-Pfad.
- [x] **M21, M22:** Fehlendes Poppler-Werkzeug auf Review und falscher temporärer
  JPEG-Ausgabepfad korrigiert; echte Linux-Gegenprobe und Gesamtlauf gehören zum
  endgültigen Freigabegate.
- [ ] **H1:** Betreiber liefert erlaubte Mailabsender; danach Freigabe anhand
  einer vom Provider überprüften Absenderidentität implementieren. Akzeptanz:
  erlaubter/verifizierter Absender importiert, fremder oder gefälschter nicht;
  abgelehnte Mail startet weder globalen Import noch KI-Aufruf. Bis dahin keine
  geratenen Adressen oder Änderungen der produktiven Mailzeitpläne.
- [ ] **M8:** Einladungstoken aus neu erzeugten HTTP-Querylinks entfernen;
  Web und Swift/Expo müssen denselben Link weiterhin verstehen. Akzeptanz:
  kein Token im Server-/Proxy-Requestpfad oder Referrer; alte gültige Links
  ausdrücklich behandeln, automatischen Haushaltsbeitritt weiter verhindern.
- [ ] **M11:** Öffentliche statische Assets versionsgebunden cachen und
  HTTP-/Service-Worker-Cache gemeinsam prüfen. Gleiche Dateigröße und
  normalisierte Release-Mtime dürfen keine alten Assets liefern. Private
  Antworten bleiben ausgeschlossen; alte Cacheversionen müssen verschwinden.
- [ ] **D1:** Alpine-CSP-Lösung ohne `unsafe-eval` vor vollständiger Umstellung
  mit sämtlichen UI-Interaktionen prüfen; keine CSP-Aufweichung ergänzen.
- [ ] **H5–H7, M17–M20:** Restliche GUI-/Texteprüfung von Web und primärem
  Swift-Client, inklusive 44-px-Zielen, fünf Rezeptzuständen und AA-Kontrast.
- [ ] **H4, M14:** Selbstlöschung und verlässlicher Hintergrundtimer sind
  verbleibende Funktionsarbeiten. Löschung braucht nachvollziehbare
  Haushaltsbesitz-/Aufbewahrungsregeln; Timer braucht echte iPhone-Abnahme.
  Keine Behauptung eines neuen App-Builds oder einer Storefreigabe.
- [ ] **C2-externe Nachweise:** Datenschutz-/Vertragsunterlagen vom Betreiber
  prüfen lassen; kein aus dem Quellcode ableitbarer Rechtsabschluss.

Lokale Prüfung und GitHub-CI sind getrennte Nachweise. Uncommittierte Änderungen
werden weder durch einen alten grünen Workflow noch durch lokale Tests zu einem
neuen CI-Lauf. Arbeitsstände im ursprünglichen Checkout bleiben erhalten.

Abschlussgate 04.10.2026 bestanden: 1.8.5 auf Review und Produktion;
819 Backendtests, 39 Webtests und 27 Expo-Tests bestanden, zwei OCR-Fälle
übersprungen. 229 Rezepte, 22 Tabellen und bestehende Konten erhalten;
132 Paketdateien geprüft. Rückfallstände sind im Nachweis dokumentiert.

Fortsetzung aus dem vierten Lauf: Installer/venv, behandelte Archiverfehler,
Auth-Worker/Requestsnapshot, Thumbnail-Revalidierung, Cart-Read, Suchkandidaten,
Restore-Timer und Expo-Zugänglichkeit sind umgesetzt. Supportlöschung F4-M5
bleibt bis zur geklärten Betreiberregel offen. Der Browserprofilordner bleibt
bis zur ausdrücklichen Bestätigung erhalten; automatische Löschfreigabe blockiert.
Abnahme-/Rolloutstatus wird ausschließlich im aktuellen Nachweis festgehalten.

## Historischer Plan 2026-08-27

Aufwand: **S** < ½ Tag · **M** ½–2 Tage · **L** > 2 Tage — mit Schätzvertrauen (hoch/mittel/niedrig).

Hinweis: Alle Findings stammen aus einem Stufe-1-Scan (statisch). B1 und C1 sollten vor der Umsetzung in Stufe 2 empirisch bestätigt werden (Verifikationsvorschläge unten) — der Fix ist aber unabhängig davon risikoarm.

---

## Paket 1 — Ausnutzbare Auth-/Config-Schwächen schließen

Warum jetzt: einziges B-Finding plus ein direkt am Login hängender Redirect; beide betreffen die extern erreichbare Web-Auth. Abhängigkeiten: keine — beide Fixes folgen bereits im Repo vorhandenen Mustern.

- [ ] **B1** (S, Vertrauen hoch) `imap_host` in die `SERVER_MANAGED`-Allowlist aufnehmen oder Passwort-Neueingabe bei Host-Wechsel erzwingen (OpenAI-Base-URL-Muster übernehmen) → `app/routes/api_config.py:112`, `app/routes/api_test.py:118`
- [ ] **C1** (S, Vertrauen hoch) `_safe_next` um Backslash-Behandlung erweitern; nur reine Pfade ohne Schema/Host zulassen → `app/main.py:442`

Akzeptanzkriterien:
- Ein `PUT /api/config` mit geändertem `mail.recipe.imap_host` und maskiertem Passwort speichert NICHT das echte Passwort für den neuen Host (Negativpfad: Testverbindung zu Fremdhost sendet keine echten Credentials).
- `_safe_next('/\evil.com')` liefert den sicheren Default (`/`), ebenso `//evil.com`, `https://evil.com` (Negativpfad); `next=/rezepte/123` bleibt erhalten.

Abnahme / Stufe-2-Verifikation:
- B1: lokale Instanz, Dummy-IMAP-Passwort; Fake-IMAP-Listener auf 993; `POST /api/test/mail` und am Listener prüfen, ob `LOGIN` das echte Passwort enthält.
- C1: `python -c "from app.main import _safe_next; print(_safe_next('/\\\\evil.com'))"`.

## Paket 2 — Datenschutz-/Doku-Konsistenz (OpenAI-Drittlandtransfer)

Warum jetzt: aktiver Datenfluss in ein Drittland bei gleichzeitig widersprüchlicher Doku — Rechenschafts-/Transparenzrisiko (DSGVO Art. 5 Abs. 2, Art. 44 ff.). Abhängigkeit: Klärung mit DSB/Betreiber (externer Nachweis), bevor Doku final formuliert wird.

- [ ] **C2** (S für Doku, Vertrauen hoch; Rechtsnachweis extern) README/Beispiel-Config/Docstrings auf OpenAI-Realität nachziehen ODER Ollama-Codepfad wiederherstellen, falls fachlich gewollt → `app/core/analyzer.py:1062`, `README.md:60,412`, `app/core/downloader.py:1`

Akzeptanzkriterien:
- Doku und `/privacy`-Seite beschreiben denselben, tatsächlich implementierten KI-Anbieter; kein verbleibender Ollama-Verweis, der nicht dem Code entspricht (Negativpfad: `grep -rn "Ollama" README.md config/ app/` liefert nur noch bewusst gewollte Treffer).
- DPA/SCC für den Drittlandtransfer liegen dokumentiert vor oder der Transfer ist deaktiviert.

## Paket 3 — Betriebs- & Hardening-Feinschliff

Warum jetzt: geringeres Risiko, unabhängig umsetzbar; sinnvoll gebündelt. Keine Abhängigkeiten.

- [ ] **C3** (S, Vertrauen hoch) Trivial-Default `changeme` im Container-Provisioning durch Zufallspasswort/Abbruch ersetzen; Root-Login deaktivieren → `proxmox/create-container.sh:11`
- [ ] **D1** (S für Fonts, Vertrauen hoch; M für Nonce-CSP, Vertrauen mittel) ungenutzte Google-Fonts-Hosts aus der CSP entfernen; mittelfristig Nonce-basierte CSP für Alpine statt `unsafe-inline`/`unsafe-eval` evaluieren → `app/security.py:185`
- [ ] **D2** (S–M, Vertrauen mittel) `pymupdf` nach oben begrenzen/pinnen, `pillow`-Spanne verengen; gehashtes Lockfile (pip-tools/uv) einführen und CI auf Frozen-Modus umstellen → `requirements.txt:16`, `.github/workflows/quality.yml:16`
- [ ] **D3** (S, Vertrauen hoch) Cloudflare-Access-/Proxy-Regel für `/metrics` verifizieren und dokumentieren; optional Bind an internes Interface → `app/routes/api_metrics.py:37`

Akzeptanzkriterien:
- Frischer Container ohne gesetztes `PASSWORD` besitzt kein bekanntes Trivialpasswort (Negativpfad: Login mit `changeme` schlägt fehl).
- `curl -sI <host>/` zeigt eine CSP ohne `fonts.googleapis.com`/`fonts.gstatic.com`; Frontend rendert unverändert.
- Zwei frische `pip install`-Läufe lösen identische `pymupdf`-Version auf.
- `curl -s <host>/metrics` von extern (über CF Access) liefert ohne gültige Access-Session kein 200.
