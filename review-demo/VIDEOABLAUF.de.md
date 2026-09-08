# Rezeptregal 1.3 – realer Review-Rundgang

Der nächste Kandidat wird auf einem iPhone-Simulator mit künstlichen Daten
aufgenommen. Die Ausgabe ist ein separates Erklärvideo für App Review, keine
fertige App-Store-Vorschau. Die tatsächliche Laufzeit ergibt sich aus dem
erfolgreichen UI-Test; sie wird nicht vorab behauptet.

| Szene | Sichtbarer Produktnutzen |
|---|---|
| Anmeldung | Rezeptregal mit dem isolierten HTTPS-Review-Server verbinden; Passwort maskiert. |
| Eingang und Archiv | Aktuelle native Tabs und der Weg von Quellen in die eigene Sammlung. |
| Pasta öffnen | Originalquelle und importierte Rezeptdaten gemeinsam zeigen. |
| Import nacharbeiten | Quellenbezug, strukturelle Hinweise und konkrete editierbare Zutaten/Schritte. Die Aufnahme speichert keine Korrektur. |
| Kochgedächtnis | Persönliche Notiz, Anpassung und Tipp fürs nächste Mal erfassen; Aufnahme zeigt den lokalen Entwurf und schließt ohne Serverübertragung. |
| Offline-Regal | Das bereits geöffnete Rezept aus der lokalen Bibliothek wieder öffnen. |
| Heute und Einkauf | Bestehende Wochenplanung und wiederkehrender Bedarf als ergänzende Abläufe. |

Die Aufnahme erzeugt benannte XCTest-Screenshot-Anhänge. Ein macOS-Lauf
exportiert diese aus dem Result Bundle. Es werden weder Mockups noch alte
Screenaufnahmen in die neuen Nachweise gemischt. Ein Netzwerk-Ausfall wird in
dieser Tour nicht simuliert; Offline- und Wiederaufnahmetests sind zusätzliche
Freigabegates in `ios-swift/APP_STORE_RELEASE_CHECKLIST.md`.

Voraussetzungen: passend aktualisierter Review-Server mit den Capabilities
`cooking-memory-v1`, `import-review-v1` und `cooking-progress-revision-v1`, der
künstliche Datensatz aus `DEPLOYMENT.md` und genau der Kandidaten-Commit. Die
dritte Capability ist für den Abgleich des lokalen Kochfortschritts mit Prüfung
der Rezeptschritte erforderlich; ohne sie bleibt dieser Fortschritt lokal.
Keine Produktionseingriffe oder Zugangsdaten in Repository und Ausgabe.

Die Tabelle beschreibt die schreibfreie Tour auf dem externen Review-Server.
Die separat aktivierte localhost-Wegwerf-Fixture ergänzt echte Speicher- und
Neustartprüfungen sowie neun weitere Screenshots. Ablauf und Sicherheitsgrenze
stehen in `ios-swift/APP_REVIEW_VIDEO.md`; der native 20-Szenenlauf und seine
visuelle Abnahme sind noch ausstehende Nachweise.
