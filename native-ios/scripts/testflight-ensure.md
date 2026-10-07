# TestFlight-Verarbeitung und private externe Verteilung

Standardmäßig wartet `testflight-ensure.mjs` nur auf den exakt angegebenen,
nicht abgelaufenen Build (`ASC_APP_ID`, Marketingversion, Buildnummer, iOS und
Uploadzeit). Die bisherige interne Option bleibt unverändert.

Der Swift-Workflow schaltet die externe Verteilung ausschließlich über den
manuellen Input `assign_external_group: true` frei, zusätzlich zu
`upload_testflight: true`. Das setzt `ASC_ASSIGN_EXTERNAL_GROUP=true`.
Erlaubt ist nur die vorhandene externe Gruppe **Privater Test** mit ID
`876c2be9-8c62-4708-a9f9-27c2caf77fb2` innerhalb derselben App.
Das Skript erstellt keine Gruppe, keine Tester und keine Einladungen und
veröffentlicht keine App im App Store oder über einen öffentlichen Testlink.

Vor Schreibzugriffen prüft es Build-Eignung, Gruppenzugehörigkeit, bestehende
Tester, Reviewstatus, Exportstatus und die vorhandenen App-Beschreibungen,
Feedbackadresse, Reviewkontaktdaten und gegebenenfalls Demo-Zugangsdaten.
Fehlende Metadaten werden ausschließlich mit Feldnamen gemeldet. Persönliche
Kontakt- und Demo-Daten werden weder ausgegeben noch verändert. Der CI-Schlüssel
muss die erforderliche App-Manager-/Admin-Berechtigung besitzen; ein vorhandener
Upload-Schlüssel beweist diese Berechtigung nicht. Ein 403 beendet den Lauf.
Freie Apple-Fehlertexte werden nicht protokolliert; Diagnosen enthalten nur den
HTTP-Kontext und maschinenlesbare Fehlercodes.

Die versionierten deutschen Testnotizen in `testflight-what-to-test-1.9.0.txt`
werden nur für den gewählten Build als `de-DE` angelegt oder aktualisiert.
Buildzuordnung und Revieweinreichung sind wiederholbar: bestehende Zuordnung,
laufendes Review und bereits freigegebene Builds werden erkannt. Die API-Antwort
wird nach der Zuordnung erneut gelesen. Vor Benachrichtigungen dürfen dem Build
keine anderen Gruppen oder einzelnen Tester zugewiesen sein. Für die bestehende
private Gruppe wird die automatische Benachrichtigung nach Freigabe aktiviert.
Ist der Build schon freigegeben und bereit, wird die Testbenachrichtigung
ausgelöst; ein bereits laufender Test wird nicht erneut benachrichtigt.

Apples dokumentierter Bedienablauf ordnet den Build zunächst der externen
Gruppe zu und bietet anschließend »Submit Review« an. Die API-Dokumentation
zur Gruppenzuordnung nennt keine Voraussetzung eines bereits genehmigten
Reviews. Das Skript folgt diesem Ablauf; die konkrete API-Annahme wird im
Lauf geprüft, nicht aus den Mocktests abgeleitet. `APP_STORE_ELIGIBLE` und alle
verwendeten externen Statuswerte sind mit Apples aktuellen API-Enums abgeglichen.

`processingState: VALID` bestätigt nur die Apple-Verarbeitung. Das Ergebnis
nennt zusätzlich `externalBuildState`, `betaReviewState`, Gruppenzuordnung und
Benachrichtigungsstatus. Nur `externalStatus: TESTING` beziehungsweise
`externalTestingAvailable: true` bestätigt `IN_BETA_TESTING`. `REVIEW_PENDING`
bedeutet, dass Apple noch entscheiden muss; der Workflow wartet nicht tagelang.
Eine angenommene Benachrichtigung bei noch verzögertem Status bleibt
`NOT_YET_TESTING`. Ein weiterer explizit autorisierter Prüflauf kann den Status
später bestätigen. Ein Review lässt sich nicht durch dieses Skript umgehen.

Die vorhandene Testgruppe, tatsächliche Schlüsselrechte und Review-Metadaten
lassen sich abschließend erst im autorisierten CI-Lauf bei Apple verifizieren.
Die lokalen Mocktests nutzen ausschließlich synthetische Antworten:
`node --test native-ios/scripts/testflight-ensure.test.mjs`.

Apple-Quellen (geprüft am 07.10.2026):

- [Externe Tester und Reviewvoraussetzungen](https://developer.apple.com/help/app-store-connect/test-a-beta-version/invite-external-testers)
- [Build einer Gruppe zuordnen](https://developer.apple.com/documentation/appstoreconnectapi/post-v1-betagroups-_id_-relationships-builds)
- [Beta-Review einreichen](https://developer.apple.com/documentation/appstoreconnectapi/post-v1-betaappreviewsubmissions)
- [Build-Teststatus lesen](https://developer.apple.com/documentation/appstoreconnectapi/get-v1-builds-_id_-buildbetadetail)
- [Bestehende Tester benachrichtigen](https://developer.apple.com/documentation/appstoreconnectapi/post-v1-buildbetanotifications)
- [Build-Eignung](https://developer.apple.com/documentation/appstoreconnectapi/buildaudiencetype)
- [Externe Statuswerte](https://developer.apple.com/documentation/appstoreconnectapi/externalbetastate)
- [Versionsbezogene Testnotizen](https://developer.apple.com/documentation/appstoreconnectapi/betabuildlocalizationcreaterequest/data-data.dictionary/attributes-data.dictionary)
