# Quellenküche für iPhone

Primärer nativer iOS-Client in Swift und SwiftUI. Der Eingang übernimmt
Rezeptlinks aus Webseiten, Pinterest, YouTube, TikTok und Instagram sowie
Fotos und PDFs. Videos werden weder geladen noch abgespielt; die Originalquelle
bleibt am Rezept sichtbar. Fehlen Zutaten oder Zubereitungsschritte, bleibt das
Rezept mit einem Hinweis zur manuellen Pflege erhalten.

Das Design ist kein Expo-Template: Navigation, Theme-Persistenz, Dark Mode,
Share Extension und Oberflächen sind native SwiftUI-Komponenten. Die vier
Farbwelten lassen sich unter **Einstellungen** pro Gerät ändern.

Über **Als Gast ansehen** ist kein separates Konto nötig. Der Gast erhält eine
signierte, rein lesende Sitzung und sieht nur Archiv und Einstellungen. Import,
Bearbeitung, Favoriten, Einkauf, Wochenplanung und Administration sind sowohl
in der Oberfläche als auch serverseitig gesperrt.

Offene Importe lassen sich nativ vollständig prüfen: Name, Beschreibung,
Portionen, Zutaten, Mengen, Einheiten, Schritte und Timer bleiben editierbar.
Ein Foto-Scan oder eine erneute KI-Analyse aktualisiert den Vorschlag, bevor
die kontrollierte Fassung als Rezept gespeichert wird.

Der Rezeptpass führt direkt in einen ablenkungsarmen Kochmodus. Er skaliert
Zutaten nach Portionen, führt schrittweise durch die Zubereitung, bietet Timer
und speichert den Fortschritt pro Konto. Ein bestätigter Abschluss wird mit
einer stabilen Idempotenz-ID genau einmal in die Kochhistorie eingetragen.

Im Rezeptfilter sind Allergiker-Infos separat von allgemeinen Tags auswählbar.
Glutenfrei, laktosefrei, eifrei und nussfrei lassen sich kombinieren; die Liste
zeigt dann nur Rezepte mit allen ausgewählten Frei-von-Tags. Diese automatisch
aus erkannten Zutaten abgeleiteten Angaben ersetzen keine medizinische Prüfung.

Rezeptbilder können einzeln oder als Altbestand neu generiert werden. Vor jeder
Ersetzung sichert der Server das vorhandene Bild mit Prüfsumme. Beim globalen
Lauf beginnt die Generierung erst, nachdem der komplette Altbestand erfolgreich
gesichert wurde. Originale lassen sich in **Rezeptpass > Bildverlauf** ansehen,
vergleichen und wiederherstellen.

## Ohne eigenen Mac testen

Das Repository enthält den GitHub-Actions-Workflow
`.github/workflows/ios-swift.yml`. Bei Änderungen unter `ios-swift/` erstellt ein
macOS-Runner das Xcode-Projekt, baut die App und führt die Unit-Tests in einem
iPhone-Simulator aus. Der Workflow kann auf GitHub zusätzlich unter
**Actions > SwiftUI iPhone App > Run workflow** manuell gestartet werden.

Ein signierter Upload ist ausschließlich über den manuellen Workflow-Schalter
`upload_testflight` möglich und läuft erst nach erfolgreichem XCTest-Job. App-
und Share-Extension-Profil werden getrennt geprüft. Der Workflow ordnet keine
Testergruppe automatisch zu.

## Optional auf einem Mac öffnen

Voraussetzungen: Xcode 16 oder neuer und XcodeGen.

```bash
brew install xcodegen
cd ios-swift
xcodegen generate
open Rezepte.xcodeproj
```

Danach in Xcode unter **Signing & Capabilities** das eigene Apple-Team wählen.
Die Serveradresse wird beim ersten Start eingegeben.

## Cloudflare Access

Für einen durch Cloudflare Access geschützten Server unterstützt die App einen
eigenen Service Token pro Gerät. Client-ID und Client-Secret werden beim Login
unter **Cloudflare-Gerätezugang** eingegeben, ausschließlich im iOS-Schlüsselbund
gespeichert und als `CF-Access-Client-Id` beziehungsweise
`CF-Access-Client-Secret` bei jeder Serveranfrage mitgesendet.

In Cloudflare Zero Trust braucht die geschützte Anwendung zusätzlich zur
normalen Browser-Policy eine **Service Auth**-Policy für diesen Service Token.
Die Zugangsdaten dürfen nicht in den Quellcode, in GitHub-Secrets für den Build
oder fest in das App-Bundle geschrieben werden. Für jedes Gerät sollte ein
eigener, einzeln widerrufbarer Token verwendet werden.

## Der Server muss HTTPS sprechen

Die App akzeptiert ausschließlich `https://` — im Simulator genauso wie auf dem
Gerät und im TestFlight-Build. Über `http` würde der Sitzungsschlüssel im
Klartext durchs Netz gehen, deshalb gibt es dafür keine Debug-Ausnahme mehr und
die Info.plist enthält kein `NSAllowsLocalNetworking` (Stand 30.07.2026).

Wer im LAN entwickelt, braucht also TLS auf dem Rezepte-Server — etwa über den
öffentlichen Hostnamen oder einen lokalen Reverse-Proxy mit Zertifikat. Ein
selbstsigniertes Zertifikat lehnt iOS ab, solange die ausstellende CA nicht auf
dem Gerät installiert und als vertrauenswürdig markiert ist.

## Vor der App-Store-Einreichung testen

1. Unit-Tests mit `Cmd-U` ausführen.
2. Im iPhone-Simulator Login, Quellen-Eingang, Rezeptpass, Bildverlauf,
   Wochenplan, Farbwelten und Einkaufskatalog prüfen. Bei aktiviertem Cloudflare Access auch Login mit
   gültigem sowie absichtlich ungültigem Geräte-Token testen.
3. Auf einem registrierten iPhone aus Xcode installieren.
4. Über **Product > Archive** einen internen TestFlight-Build hochladen.

Die App enthält keine WebView und keinen Video-Player.
