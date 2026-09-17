# App-Review-Notizen – Rezeptregal 1.2.0

Stand: 15. September 2026. Korrekturvorbereitung, noch kein Upload und keine
erneute Einreichung.

## Betroffene Einreichung

- Apple-ID: `6803595058`
- Submission: `50e307f2-a8c1-4284-8136-c226d3f81867`
- Geprüft: `1.2.0 (2307)` am 15. September 2026
- Quellbasis: `c93567b5afb7645cc7331f9a4e8c2d66a80fdc6e`
- Beanstandet: Namen, Support-URL und unklarer Geschäftsablauf.
  Aus den Ausschnitten folgt keine Freigabe hinsichtlich Guideline 4.3.

## Korrekturen im Kandidaten

- App: Rezeptregal; Share-Erweiterung: Rezeptregal teilen.
- Bundle-IDs, App Groups und eingereichtes Icon bleiben unverändert.
- Zentrales Supportformular mit ausdrücklich freizugebender Portal-URL.
- Gastzugang bleibt lesend; normale Konten dürfen nicht importieren.
- Ein vom Administrator separat ausgestelltes Share-Token ist eine eigene
  Berechtigung für Kurzbefehle, kein normales Benutzerkonto und kein Kauf.

## Support-Metadatenentwurf

Ziel für das Feld „Support-URL“ in allen App-Store-Lokalisierungen:
`https://support.zimlab.org/?module=rezeptregal`.
Nur lokal vorbereitet. Erst nach verifiziert öffentlichem Formular und
Owner-only-Verwaltung in App Store Connect speichern.

## Technische Hinweise für App Review

Rezeptregal is a native SwiftUI client for a recipe server. Enter the provided
HTTPS server address. Guest access allows read-only browsing without creating
an account; it does not bypass additional server access controls. Signed-in
accounts can use the permitted recipe, meal-planning and shopping features.
Accounts are managed by the server administrator; there is no self-registration
in the app. Import and inbox management require administrator access.

For support, open Help and Contact on the login screen or in Settings.
The public support page does not require a Rezeptregal or GitHub account.

Provide the actual review server and working review-account credentials in
App Store Connect's designated fields. Test both a normal account and an
administrator account against the exact submitted build and server.

## Noch nicht als Tatsache an Apple senden

- Kostenfreiheit: Betreiberbestätigung für App, Funktionen, Konto und Server
  fehlt. Fehlender StoreKit-Code ist kein Nachweis des Geschäftsmodells.
- Persönliche Bewertungen: derzeit gemeinsame Werte am Rezept, nicht je Nutzer.
- Support bereits veröffentlicht / Metadaten bereits geändert / Build bereits
  hochgeladen: erst nach tatsächlicher Durchführung behaupten.
- Zusatzfunktionen aus `codex/appstore-improvements`: nicht in diesen Kandidaten
  übernommen.

Die fünf offenen Geschäftsmodellfragen und Freigabeschritte stehen in
`docs/apple-review/2026-09-15-response-and-release.md`.
