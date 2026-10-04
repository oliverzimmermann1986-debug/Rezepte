# Quellenküche 1.8.0: Haushalte und globale Rezepte

Stand: 03.10.2026. Backend und Web sind auf der Produktionsinstanz CT200
bereitgestellt. Der integrierte Quellstand liegt in diesem verwalteten Worktree:
`C:\Users\Entwickler\.codex\worktrees\household-release\Rezepte`.
Der ursprüngliche Checkout enthält ältere, unverändert erhaltene Arbeitsstände.
Für weitere Arbeiten diesen Worktree verwenden; nicht den ursprünglichen Checkout
auf die neue Datenbank anwenden.

## Verhalten

- Gäste sehen globale Rezepte und können ein Konto erstellen. Serverseitige
  Schreibsperren schützen Rezepte und private Haushaltsdaten.
- Jedes Konto erhält einen Haushalt. Der Eigentümer kann eine zweite Person mit
  eigener Anmeldung über einen widerrufbaren, einmal verwendbaren Link einladen.
  Eine Einladung ist sieben Tage gültig. Beim Beitritt eines bestehenden Kontos
  werden dessen private Daten in den gemeinsamen Haushalt übernommen.
- Private Rezepte, Favoriten, Bewertungen, Einkauf, Planung und Kochfortschritt
  sind je Haushalt getrennt. Globale Rezepte bleiben für alle lesbar; nur
  Administratoren verändern sie.
- Ein privater Import mit bereits global vorhandenem Link speichert einen Verweis
  in der Haushaltssammlung. Er löst keinen zweiten Download und keine erneute
  KI-Auswertung aus. URL-Aliase und konkurrierende Importaufträge sind berücksichtigt.
- Web, primärer SwiftUI-Client und älterer Expo-Client haben Registrierung,
  Einladungen und Sammlungsansichten. Sitzungswechsel verwerfen alte Antworten
  und private Ansichten.

## Bereitstellung und Datenprüfung

Produktiv: Backend 1.8.0, Datenbankschema 261, Webdienst und beide bisherigen
Timer aktiv; Aktivierung und Zeitpläne sind erhalten. Die Kontenanmeldung ist
aktiviert. Vorhandene Benutzer, Passworthashes und Rollen wurden beibehalten.

Alle 229 aktiven Rezepte sind als globale Rezepte erhalten. Die bestehende
Papierkorb-Bereinigung entfernte nach dem Start zehn bereits über 30 Tage alte
gelöschte Rezepte samt zugehörigen Zutaten, Schritten und Tags. Elf Quelldaten
wurden durch die bestehende Synchronisation aus den Ordnerzeitstempeln ergänzt.
Der Vergleich zum geschützten Datenbanksnapshot erlaubt ausschließlich diese
bestehenden Wartungsschritte; aktive Rezeptinhalte und die übrigen geprüften
Bestandsdaten blieben unverändert.

SQLite-Integrität, Fremdschlüssel, alle 126 installierten Dateiprüfsummen,
Gesundheits- und Bereitschaftsprüfung, Registrierung, Gastanmeldung und sieben
Gast-Schreibsperren wurden am laufenden Dienst geprüft. Cloudflare Access bleibt
vorgeschaltet; ein anonymer öffentlicher Gesundheitsabruf erhält HTTP 302.
Es wurden keine echten Konten oder Einladungen für die Prüfung angelegt und
keine Einladungsnachrichten versendet.

Die Review-Instanz CT117 wurde mit sieben Demorezepten erfolgreich geprüft und
anschließend wieder ausgeschaltet und ausgehängt, wie zu Beginn. Ihr Importtimer
bleibt deaktiviert. Der Review-Datenaufbau berücksichtigt jetzt Haushalte und
erhält andere Haushaltsdaten auch bei identischen Produktnamen.

Archiv: `.tmp/releases/1.8.0-households/rezepte-1.8.0.tar.gz`.
SHA-256: `6a513e461d017d0a473db84e3e406aee08ec5392ebaf99a24ece2c3baa59424f`.
Das Backendarchiv enthält keine nativen Clientquellen.

Geschützte Rückfallsicherung auf CT200:
`/opt/scrapper-code-backups/state-20261003-210928-13027`.
Auf CT117: `/opt/scrapper-code-backups/state-20261003-210707-1435`.
Diese Verzeichnisse enthalten private Laufzeitdaten und gehören nicht in Git.
Der Updater stellt bei einem fehlgeschlagenen Rollout Code, Datenbank,
Konfiguration und Dienste wieder her. Ein automatischer Rückfall wurde während
der ersten Review-Prüfung tatsächlich ausgeführt und danach kontrolliert.

## Nachweise und Grenzen

- Gesamte isolierte Backend-Suite: **739 bestanden, 2 übersprungen** in 208,82 s.
  Beide übersprungenen OCR-Prüfungen benötigen das lokal fehlende Tesseract.
- Web-Unit-Tests: **31 bestanden**. Synthetische Browserabläufe für Gastzugang,
  Konto und Wiederverwendung globaler Links wurden einschließlich 320, 390 und
  1440 Pixel breiter Ansichten geprüft.
- Älterer Expo-Client: **16 Tests bestanden**, TypeScript-Prüfung, Lint und
  iOS-JavaScript-/Hermes-Export erfolgreich. Der Export ist keine signierte IPA.
- Primärer SwiftUI-Client: **86 Prüfungen der nativen Quellen bestanden**.
  Alle **43 Swift-Dateien** wurden mit Tree-sitter Swift 0.7.3 ohne Syntaxfehler
  geparst. Das ersetzt weder Swift-Typprüfung noch einen Xcode-Build.
  Neue XCTest-Fälle decken Registrierung, Einladungslinks, Sammlungsfilter,
  Referenzen, DTO-Kompatibilität und verspätete Antworten nach Kontowechsel ab.
- Ruff und `git diff --check` erfolgreich; der Updater besteht die Shell-Syntaxprüfung.

Die SwiftUI-App wurde hier unter Windows **nicht mit Xcode gebaut oder im
Simulator gestartet**. XCTest wurde nicht ausgeführt. Eine neue IPA und ein
TestFlight-Upload sind nicht erfolgt. Der vorhandene macOS-Workflow
`.github/workflows/ios-swift.yml` führt Build und XCTest aus, sobald der
Quellstand dort verfügbar ist. Vor einem nativen Upload zusätzlich Gastzugang,
Registrierung, Einladung, Kontowechsel und private Importansichten im Simulator
prüfen. Es wurde kein Git-Commit oder Push ausgeführt.

Der maschinenlesbare Laufbericht, Dateimanifest und lokale Prüfhelfer liegen
unter `.tmp/releases/1.8.0-households/`; `.tmp` ist von Git ausgeschlossen.
