# Rezeptregal 1.3 – echte Screenshots und Review-Video

Die vorhandene `RezepteReviewVideo`-Scheme führt den nativen UI-Rundgang aus
und hält elf benannte Screenshots als XCTest-Anhänge fest, mit der lokalen
Wegwerf-Fixture zwanzig. Das Skript zeichnet
gleichzeitig ein separates Review-MP4 auf und exportiert anschließend die
tatsächlichen Anhänge samt Manifest aus dem Result Bundle. Es erzeugt keine
Mockups und lädt nichts zu App Store Connect hoch.

## Voraussetzungen

- macOS mit Xcode 16 oder neuer, XcodeGen und verfügbarem iPhone-Simulator.
- Passender isolierter Review-Server mit künstlichen Daten; die öffentliche
  `/api/system/info`-Antwort muss `cooking-memory-v1` und `import-review-v1`
  enthalten. Das Skript bricht andernfalls vor Aufnahmebeginn ab.
- `APP_REVIEW_PASSWORD` als geschützte Umgebungsvariable; niemals in Git.
- Optional `APP_REVIEW_SERVER`, `APP_REVIEW_USERNAME`, `APP_REVIEW_VERSION`
  und `APP_REVIEW_DEVICE_TYPE`. Standard ist iPhone 16 Pro Max; die beim
  späteren Apple-Upload geforderte Auflösung muss separat geprüft werden.

## Lokal auf macOS

Im Ordner `ios-swift` mit bereits gesetzter geschützter Passwortvariable:

```bash
xcodegen generate
bash scripts/record-review-video.sh
```

Jeder Lauf erstellt einen eigenen Simulator und eigene Ausgabeordner. Nur
dieser Simulator wird am Ende entfernt; vorhandene Simulatoren, Daten und alte
Aufnahmen bleiben erhalten.

Ausgaben bei Erfolg:

- `artifacts/review-<Lauf>/Rezeptregal-App-Review-1.3.0.mp4`
- `artifacts/review-<Lauf>/screenshots/` mit exportierten Anhängen/Manifest
- `artifacts/review-<Lauf>/ReviewResults.xcresult` mit vollständigem Testnachweis
- `artifacts/review-<Lauf>/capture-source.txt` mit Commit, Version und Gerät
- `build/Review-<Lauf>/DerivedData/Build/Products/Debug-iphonesimulator/Rezepte.app`

Die im Manifest benannten Motive `04/05-import-*`,
`06-persoenliches-kochgedaechtnis-entwurf` und `07-auf-diesem-iphone` zeigen die
neuen Abläufe. Auf einem externen Review-Server werden der persönliche
Notizentwurf und die Importfelder nicht serverseitig gespeichert. Dieser
externe Rundgang bestätigt deshalb keine Persistenz oder Offline-Synchronisierung;
diese Nachweise werden separat in
`APP_STORE_RELEASE_CHECKLIST.md` verlangt.

## Isolierte lokale Aufnahme ohne Server-Deployment

`bash ios-swift/scripts/record-local-review.sh` startet auf macOS einen
temporären HTTPS-Server ausschließlich auf `127.0.0.1:18443`. Er verwendet
sechs künstliche Rezepte aus dem vorhandenen Review-Datensatz, eine frische
Datenbank und ein zufälliges lokales Passwort. Die normale App-Authentifizierung
bleibt aktiv. Die Produktionswerkzeuge mit ihrem Hostschutz werden nicht
aufgerufen oder verändert.

Für TLS entsteht ein eintägiges localhost-Zertifikat. Nur der eigens erstellte
Simulator vertraut diesem Zertifikat; ATS und der Client werden nicht umgangen.
Nach der Aufnahme werden lokaler Server, temporäre Daten und dieser Simulator
entfernt. Es gibt kein externes Review-Passwort und kein Deployment.

Nur dieses Skript aktiviert `APP_REVIEW_LOCAL_FIXTURE=1`. Der UI-Test und das
Aufnahmeskript verweigern diesen Modus, wenn die URL nicht HTTPS mit dem exakten
Host `localhost` ist. Es gibt keine Test-Abkürzung in der Produktions-App: alle
Schreibaktionen laufen durch die normalen Bedienelemente und die echte Anmeldung.

Die zusätzliche lokale Sequenz speichert den vorher eingegebenen Notizentwurf,
wartet auf eine bestätigte Servernotiz und öffnet sie nach einem App-Neustart.
Danach wird die erste Zutat von „Pasta“ zu „Pasta nach Wahl“ mit einer Begründung
umbenannt; der Vergleich, die Übernahme, der erneut geladene Wert und der Erhalt
der Originalquelle werden geprüft. Schließlich hakt die Tour den ersten
Kochschritt ab, startet die App neu, öffnet das Rezept aus dem Offline-Regal und
setzt bei Schritt 2 mit einem erledigten Schritt fort. Die realen Anhänge 12–20
zeigen die Vorher-/Nachherzustände. Das Netz wird dabei **nicht** getrennt:
Offline-Fallback, spätere Synchronisierung und Konto-Isolation bleiben eigene
Geräte-/Backendprüfungen. Ein vorbereiteter Test ist noch kein bestandener Lauf.

Der GitHub-Workflow `ios-swift.yml` bietet bei manueller Ausführung
`capture_visuals: true` (Standard). Der eigene Job `visual-review` läuft nach
den nativen Tests, benötigt keine Release-Secrets und lädt nur reale
Aufnahme-/Diagnoseartefakte hoch. `upload_testflight` bleibt separat und
standardmäßig **false**. Lokal müssen die Python-Abhängigkeiten aus
`requirements.txt` installiert sein. Dieser Fixture-Rundgang ersetzt weder die
spätere Prüfung des tatsächlichen Review-Servers noch einen Offline-Gerätetest.

## Codemagic

Der Workflow `ios-review-video` verwendet die geschützte Gruppe `app_review`.
Er wird wie bisher durch ausdrücklich veröffentlichte Tags mit Präfix
`review-video-` gestartet; das Bearbeiten dieser Dateien startet keinen Build.
Der Workflow sammelt MP4, PNG/Manifest, Result Bundle, Logs und Simulator-App
aus den neuen laufbezogenen Ordnern.

Vor einer späteren Release-Aufnahme den künstlichen Datensatz gemäß
`review-demo/DEPLOYMENT.md` prüfen. Die Aufnahme zeigt die aktuellen Tabs
**Eingang / Archiv / Heute / Einkauf / Einstellungen**. Administration und
Rezept-ID sind keine zentrale Szene mehr. Menü-Dirigent ist keine Neuerung und
kein Bestandteil dieses Rundgangs.

Ein erfolgreicher Test ist noch keine visuelle Abnahme: alle Screenshots und
das vollständige MP4 ansehen, Tastaturüberlagerungen/Fehler ausschließen und
den Kandidaten abgleichen. Das Review-MP4 ist nicht automatisch eine zulässige
App-Store-Vorschau; dafür wäre ein gesonderter passender Export erforderlich.
