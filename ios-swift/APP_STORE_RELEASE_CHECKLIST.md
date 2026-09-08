# Rezeptregal 1.3 – nächste Version prüfen und belegen

Status dieses Dokuments: vorbereitet, noch keine native Aufnahme, kein Upload
und keine neue Einreichung durch diese Änderungen. Die bestehende Übermittlung
bleibt unberührt. Jedes Kästchen verlangt einen überprüften Nachweis.

## Kandidat und Server

- [ ] Kandidaten-Commit, Version und neue Buildnummer dokumentieren.
- [ ] Native Swift-/XCTest- und UI-Prüfungen auf macOS erfolgreich ausführen.
- [ ] Isolierten Review-Server erst im freigegebenen Release-Ablauf auf den
  passenden Backendstand bringen; künstliche Daten und Zugang prüfen.
- [ ] `/api/system/info` meldet `cooking-memory-v1`, `import-review-v1` und
  `cooking-progress-revision-v1`. Ohne die dritte Capability darf kein
  serverseitiger Abgleich des lokalen Kochfortschritts zugesichert werden.
- [ ] Erststart mit dem echten Review-Zugang gelingt; Gastzugang und Serverpflicht
  sind in den Store- und Review-Texten korrekt beschrieben.

## Neue Abläufe auf dem Gerät

- [ ] Importprüfung zeigt Originalquelle und strukturelle Probleme; eine
  bestätigte Admin-Korrektur bleibt nach erneutem Laden bestehen.
- [ ] Ein reguläres Konto kann einen Korrekturvorschlag einreichen, aber nicht
  selbst die Admin-Freigabe ausführen. Gastzugang bleibt schreibgeschützt.
- [ ] Persönliche Notiz, Anpassung und nächster Tipp bleiben nach erneutem
  Laden erhalten; ein anderes Konto sieht sie nicht.
- [ ] Kochreflexion nach Abschluss und gespeicherte Schritt-Tipps prüfen.
- [ ] Bereits geöffnetes Rezept mit Zutaten und Schritten im Offline-Modus
  öffnen; wieder aufnehmen, Schritte abhaken und App neu starten.
- [ ] Netzwerkfehler meldet den Nutzer nicht ab. Nach Wiederverbindung wird
  vorgemerkter Fortschritt genau einmal beziehungsweise konfliktbewusst
  synchronisiert. Zwischenzeitliche Änderungen am Rezept prüfen.
- [ ] Bei einem Server ohne `cooking-progress-revision-v1` bleibt ungesendeter
  Kochfortschritt lokal erhalten und ein Updatehinweis erscheint. Nach einem
  passenden Serverupdate den Abgleich und geänderte Rezeptschritte erneut prüfen.
- [ ] Konto-/Serverwechsel zeigt keine fremden lokalen Rezepte oder Notizen.
- [ ] Timer bei Bildschirmwechsel, App-Hintergrund und Gerätesperre prüfen;
  keine Hintergrundzuverlässigkeit bewerben, bevor sie belegt ist.

## Neue Medien

- [ ] `scripts/record-review-video.sh` mit dem Kandidaten und künstlichem
  Review-Datensatz erfolgreich ausführen; kein übersprungener UI-Test.
- [ ] Exportierte XCTest-Anhänge visuell prüfen: aktuelle Tabs **Eingang,
  Archiv, Heute, Einkauf, Einstellungen**, Rezeptregal-Name, keine Fehler,
  keine abgeschnittenen Inhalte und keine privaten Daten.
- [ ] Screenshots müssen dieselbe Version wie das Binary zeigen; alte Bilder
  mit **Rezepte / Wochenplan / Einkauf / Admin** aussortieren.
- [ ] Simulator-Auflösung mit den beim Upload angezeigten Apple-Anforderungen
  abgleichen; weitere benötigte Gerätegrößen separat aufnehmen.
- [ ] Persönliches Kochgedächtnis, Importkorrektur und lokale Bibliothek in
  den ersten drei Motiven zeigen.
- [ ] Review-MP4 vollständig ansehen. Es ist ein separates Review-Video;
  eine App-Store-Vorschau erfordert einen eigenen passenden Schnitt/Export.

## Nächste externe Übermittlung

- [ ] Store-Texte nur mit nachgewiesenen Funktionen der Kandidatenversion
  übernehmen und die funktionierenden Support-/Datenschutzlinks prüfen.
- [ ] Passwort ausschließlich im geschützten Review-Anmeldefeld hinterlegen.
- [ ] Neue Screenshots, Version und exakt richtige Buildnummer zuordnen.
- [ ] Review Notes anhand des tatsächlichen Gerätrundgangs abgleichen.
- [ ] Upload/Übermittlung und resultierenden Apple-Status erst nach der
  entsprechenden Release-Aktion bestätigen; keine Annahmegarantie geben.

Diese Datei und die vorbereiteten Aufnahmetests erledigen keine dieser externen
Aktionen automatisch. Insbesondere werden keine laufende Prüfung abgebrochen,
Metadaten gespeichert, Builds hochgeladen oder Apple-Nachrichten versendet.
