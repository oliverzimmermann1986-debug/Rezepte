# Version 1.10.0

## Vier Rollen

- **Gast:** globale Rezepte lesen, keine Änderungen oder Importe.
- **Benutzer:** eigene Rezepte und Varianten, Haushalt, Einkauf und Wochenplan nutzen.
- **Vollbenutzer:** alle Benutzerfunktionen sowie private Link-, Foto- und PDF-Importe einschließlich Prüfung der eigenen Importvorschläge.
- **Admin:** alle Funktionen, globale Importe, Benutzerverwaltung und Systemwartung.

Admins können alle vier Rollen vergeben. Neue Registrierungen mit Passwort,
Apple oder Google erhalten weiterhin die Rolle Benutzer. Bestehende Benutzer
werden nicht automatisch hochgestuft. Ein Rollenwechsel widerruft vorhandene
Sitzungen; nach erneuter Anmeldung gelten die neuen Rechte.

Auch angemeldete Gastkonten können ihre eigene Anmeldung und Sicherheit
verwalten. Ihre fachlichen Zugriffe bleiben ausschließlich lesend.

## E-Mail-Import entfernt

Postfachabruf, IMAP-Konfiguration, Verbindungstests, Abrufzeitpläne und die
zugehörigen Dienst-/Timer-Einstiegspunkte entfallen vollständig. Direkte Importe
über Links, Fotos, PDFs und die Teilen-Funktion bleiben erhalten. Vorhandene
Rezepte und historische Quelldaten werden nicht gelöscht.

## Oberflächen

Web, SwiftUI und Expo zeigen die neuen Rollen und erlaubten Funktionen an.
Vollbenutzer können importieren, ohne Zugriff auf die Administration zu erhalten.
Die iPhone-App erhält Version 1.3.0. Webdateien verwenden zusätzlich die
Releaseversion für ihren Cache-Schlüssel, auch bei normalisierten Dateizeiten.
