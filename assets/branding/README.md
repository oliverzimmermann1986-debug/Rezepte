# App-Icon

`app-icon-master.png` ist das unveränderte, am 8. Oktober 2026 generierte
1254 × 1254 Pixel große Motiv: gelbes Rezeptbuch mit Holzkochlöffel auf
anthrazitfarbenem Hintergrund. Es ist die gemeinsame Quelle für SwiftUI,
die Expo-App und die PWA. Das ursprüngliche generierte Bild bleibt zusätzlich
außerhalb des Repositorys erhalten.

Die sechs ausgelieferten PNGs sind sRGB mit drei Farbkanälen, ohne Transparenz
und ohne eingebrannte runde Ecken. Der technische Export verwendet Lanczos3
und sharp 0.35.4. SwiftUI, Expo-App und Expo-Splash verwenden dasselbe
1024-Pixel-Bild. Die normalen PWA-Icons sind 192 und 512 Pixel groß.

Beim 512-Pixel-Maskable-Icon liegt das vollständige Motiv auf 430 × 430 Pixeln
mit einem umlaufenden Rand von 41 Pixeln in `#191813`. Damit bleiben die
äußersten Buchkanten innerhalb des sicheren Kreises mit Radius 40 % der
Iconbreite. Es wird keine Maskenform in das Bild gerendert.

## Reproduzieren

Im Repository-Stamm mit Node.js und sharp 0.35.4 ausführen. Die Abhängigkeit
kann getrennt von den App-Abhängigkeiten im ignorierten `.tmp` liegen:

```powershell
npm.cmd install --prefix .tmp/icon-tools --no-save sharp@0.35.4
$env:REZEPTE_SHARP_MODULE = (Resolve-Path .tmp/icon-tools/node_modules/sharp).Path
node tools/generate_app_icons.cjs
```

Alternativ kann `sharp` regulär im Node-Modulsuchpfad liegen. Der Export
ändert ausschließlich die sechs bestehenden Icon-PNGs und bewahrt den Master.
Die PWA-Icon-Adressen sind für diese Gestaltung mit `v=1.9.2` versioniert.
