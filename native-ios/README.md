# Rezepte für iPhone

TypeScript-/Expo-App für den bestehenden FastAPI-Rezepteserver. Die App lädt
keine TikTok- oder Instagram-Medien herunter; einzelne Posts werden nur als
externe Quellen gespeichert und bei unvollständigen Angaben manuell gepflegt.

## Entwicklung

```powershell
npm ci
npm run typecheck
npm run lint
npx expo export --platform ios
```

Die freigegebene App wird ohne lokalen Mac über EAS Build erstellt und zuerst
in TestFlight geprüft. Der manuelle GitHub-Workflow übernimmt Build und Upload:

```powershell
npx eas-cli@16.28.0 build --platform ios --profile production
```

Beim ersten Start werden Server-Adresse, Benutzername und Passwort abgefragt.
Die Release-App verbindet sich ausschließlich mit den in `app.json` unter
`extra.allowedApiUrls` freigegebenen HTTPS-Rezeptservern. Aktuell sind das der
Produktionsserver und der isolierte App-Review-Server; beliebige Hosts bleiben
gesperrt. Sitzungstoken und optionale Cloudflare-Service-Zugangsdaten
liegen im iOS-Schlüsselbund und werden beim Abmelden zusammen mit privaten
Bildcaches entfernt; die Serversitzung wird widerrufen.

## Funktionsumfang

- native iOS-Tab-Navigation
- Rezeptsuche und Filter für manuelle Pflege
- Rezeptdetails ohne Video, mit externem Quellenlink
- manuelle Zutaten- und Schrittpflege
- Bildwechsel mit iOS-Zuschnitt und HEIC-sicherer JPEG-Normalisierung
- lokaler Foto- und PDF-Import aus Fotos bzw. Dateien
- vollständiger Editor für unklare Importe inklusive Zutaten und Schritten
- Prüfmarke für kontrollierte Zutatenlisten
- Sternebewertungen direkt am Rezept
- Schritt-Timer
- Favoriten und Übernahme in die Einkaufsliste
- gemeinsame Einkaufsliste
- Wochenplan inklusive Wocheneinkauf
- Admin-Übersicht, Link-only-Direktimport, Postfachlauf und manuelle Prüfung
- sicherer Bearer-Login über `/api/auth/login`

## App-Store-Vorbereitung

Vor der Einreichung müssen `bundleIdentifier`, App-Icon, Datenschutz-URL,
Support-URL, Screenshots, Altersfreigabe und die Review-Zugangsdaten in
App Store Connect final gepflegt werden. Der Reviewer benötigt Zugriff auf
einen erreichbaren Testserver.
