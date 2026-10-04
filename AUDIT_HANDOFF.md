# Priorisierte Übergabe nach sechs Prüfläufen

Aktueller Arbeits- und bestätigter Livestand: `household-release`,
1.8.8 / Schema 265 auf Review und Produktion mit identischem Paket geprüft.
Originalcheckout und weitere WIP-Worktrees nicht überschreiben. Kein Commit,
Push oder aktueller GitHub-CI-Nachweis. Aktueller Abschlussnachweis:
[1.8.8](AUDIT_FOLLOWUP_1.8.8.md); historische Nachweise bleiben erhalten.
Alle Befunde inklusive Historie: [AUDIT.md](AUDIT.md).

Fünfter Lauf: [Gegenprüfung 1.8.7](AUDIT_FOLLOWUP_1.8.7.md). Auth-Typen,
PDF-Mengen, KI-Belege/Abbruch/Kontingente, Suche, Cart-Merges, Testpfade und
Quellcover sind korrigiert. Vollständiger Abschlusslauf und Rollout bestanden;
zehn Fachlogik-/KI- und 25 Lifecycle-Prüfungen je installiertem Stand grün.

Sechster Lauf: Formular-Origin, Logout-CSRF, NAT-/Proxy-Limits, SQLite-503,
Worker-Abstürze, Detail-Verbindungen, Papierkorbquellen und Config-Pfade sind
korrigiert. Zehn neue Auth-/Last-/Upgrade-Probes je installiertem Stand bestanden.
Echter Chromium-Gastzugang direkt auf Produktion jetzt erfolgreich; öffentliche
Cloudflare-Domain und echtes Benutzerlogin separat offen. Alter Rootcheckout
bleibt Schema 232 und darf nicht deployed werden; integriert sind Migration 261
und der geprüfte Upgradepfad 260 → 265 mit Backup und Restore alter Backups.

Abnahme: 928 Backendtests, 39 Webtests, 32 Expo-Tests bestanden; drei lokale
Skips, Coverage 64,51 %. 148 Paketdateien, Hash und Rückfallstände im Nachweis.
Produktion behält 229 Rezepte, Konten und 22 Tabellen; Health/Readiness 200,
Service und beide Timer aktiv. Review ausgeschaltet und ausgehängt. Separates
Archiv in dieser Runde unverändert: bytegleicher Quellstand, kein neuer Rollout.
Die 15 Linux-Installer-/SIGTERMprüfungen und 215 erhaltenen Archivdateien sind
der Nachweis aus 1.8.6; der Ein-Job-Worker und seine Konfiguration bleiben erhalten.

Lokaler Test-Nebeneffekt: Vor der Pfadkorrektur verwendete ein neuer Browser-
Server versehentlich `C:\opt\scrapper\data\scrapper.db`. Schema 265 und vier
bekannte synthetische Browserbenutzer sind dort dokumentiert; nichts bereinigt.
Nach der Korrektur bestätigen voller Lauf und Abschlussgate einen unveränderten
DB-Hash. Echte Browser-Tests blockieren jetzt fremde DB-Pfade vor Zugriff.

## Noch zu erledigen, nach Priorität

1. **H1 – Mailabsender:** Erlaubte Adressen sind beim Betreiber angefragt.
   Providerbestätigte Absenderauthentifizierung und Haushaltszuordnung prüfen;
   eine ungesicherte `From`-Allowlist reicht nicht. Bestehende Konten und Timer
   bis zur geklärten Regel erhalten; Tests mit gefälschten Headern einplanen.
2. **H4 – Selbstlöschung:** Besitzer-, Mitbewohner- und Haushaltsregeln festlegen.
   Für geteilte Konten Datenübernahme/Erhalt und Token-/Einladungswiderruf prüfen.
   Adminlöschung ist bereits gegen verwaiste private Daten gehärtet, ersetzt
   jedoch keinen vollständigen Selbstlöschungsprozess.
3. **F4-M5 – Supportlöschung:** Aktuellen separaten Support-Checkout feststellen,
   Bearbeitung einer Löschanfrage mit Referenz und Identitätsprüfung definieren.
   Keine frei erfundene Retention-Frist und keine automatische Löschung realer
   Tickets ohne Betreiberregel. CT 118 ist ein eigener Dienst.
4. **M14 – Timer im Hintergrund:** Lokale Benachrichtigung und Abbruch bei
   Rezept-/Konto-/Serverwechsel auf iPhone prüfen. Keep-Awake in Expo löst die
   Benachrichtigung bei gesperrtem Gerät nicht.
5. **H5–H7, M17–M20, F4-H5/H6 – Geräte-GUI:** Primärclient ist `ios-swift`;
   Expo ist Bestandsclient. VoiceOver, große Systemschrift, Kochmodus,
   Headernavigation, 44-px-Ziele und alle fünf Rezeptzustände prüfen. Lokale
   TSX-/Kontrasttests sind kein neuer Geräte- oder Swift-Buildnachweis.
6. **M8, M10 – Logs/Tokens:** Einladungstoken in Access-Logs und überflüssige
   fehlgeschlagene Loginangaben reduzieren. Keine reale Logzeile mit Identität,
   Passwort oder Token in Nachweise übernehmen.
7. **M11, D1 – Web:** Offline-/Cacheverhalten und CSP-Abhängigkeit von
   `unsafe-eval` gesondert prüfen. Thumbnail-Revalidierung F4-H3 ist behoben;
   sie ersetzt keine komplette Service-Worker-Überarbeitung.
8. **C2 – Betreiberunterlagen:** Datenschutz-/Anbieter-/Vertragsnachweise sind
   außerhalb des Quellcodes zu prüfen. Keine rechtliche Freigabe aus Tests ableiten.

## Bereits korrigiert und erneut abgesichert

K1–K4: private PDFs, Tupel-/Row-Migration, IMAP-TLS und Punktpfade. Dazu Bodylimits,
Importkontingente, Haushaltsfreigaben/SSE, stabile Benutzer-ID beim Kochen,
Bildverarbeitung/Veröffentlichung, Canonical-Origin, PDF-/Downloadgrenzen und
die Web-Backup-/Beitritts-/Doppelsendefehler. Der vierte Lauf ergänzt
Auth-Worker/Snapshot, Thumbnail-304, Cart-Read, Suchkandidaten, Restore-Timer,
Archiver-Installer/Abbruch und Expo-Zugänglichkeit. Details und Grenzen in den
verlinkten Nachweisen; alte Fehlerzahlen nicht als aktuellen Zustand wiederholen.

## Sichere Weiterarbeit

- Tests über `tools/run_isolated_tests.py` mit temporärer DB/Config; kein KI-,
  Mail- oder echter Videoauftrag zur Prüfung.
- Linux-Archiver-Probe: `python tools/probe_video_archiver_install.py` als root
  im bestehenden Testcontainer. Verwendet nur temporäre Verzeichnisse, echte
  venv-Launcher und simulierte Paket-/Servicegrenzen.
- Release erst nach grünen lokalen Gates, auf Review prüfen, dann denselben
  Hash auf Produktion; Konten, 229 Bestandsrezepte und 22 Tabellen bewahren.
- Browserprofil `.codex-tmp-logo-edge-pot` bleibt bis zur ausdrücklichen Antwort
  erhalten: automatische Löschfreigabe war blockiert. Ignore-Regel ist ergänzt.
