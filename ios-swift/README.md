# Rezepte für iPhone

Native iOS-App in Swift und SwiftUI. Videos werden weder geladen noch
abgespielt. Ein TikTok-/Instagram-Link wird ausschließlich als externer
Quelllink geöffnet. Fehlen Zutaten oder Zubereitungsschritte, bleibt das
Rezept mit einem Hinweis zur manuellen Pflege sichtbar.

## Ohne eigenen Mac testen

Das Repository enthält den GitHub-Actions-Workflow
`.github/workflows/ios.yml`. Bei Änderungen unter `ios-swift/` erstellt ein
macOS-Runner das Xcode-Projekt, baut die App und führt die Unit-Tests in einem
iPhone-Simulator aus. Der Workflow kann auf GitHub zusätzlich unter
**Actions > Native iOS > Run workflow** manuell gestartet werden.

Für einen signierten TestFlight-Build werden später eine aktive
Apple-Developer-Mitgliedschaft, der App-Store-Connect-Eintrag und
Signierungsdaten als verschlüsselte GitHub-Secrets benötigt.

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
2. Im iPhone-Simulator Login, Rezepte, manuelle Pflege, Wochenplan und
   Einkaufsliste prüfen.
3. Auf einem registrierten iPhone aus Xcode installieren.
4. Über **Product > Archive** einen internen TestFlight-Build hochladen.

Die App enthält keine WebView und keinen Video-Player.
