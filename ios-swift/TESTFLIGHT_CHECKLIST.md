# Testplan vor der App-Store-Einreichung

## 1. Automatische Prüfungen

- In Xcode `Product > Test` ausführen.
- App im Debug- und Release-Schema bauen.
- Xcode-Warnungen und den Organizer-Validierungsbericht prüfen.
- Auf ein leeres Testkonto und ein Konto mit vielen Rezepten testen.

## 2. Simulator

Mindestens ein kleines und ein großes aktuelles iPhone testen:

- Anmeldung, falsches Passwort und abgelaufene Sitzung
- Anmeldung mit `http://`-Adresse: muss mit dem Hinweis auf HTTPS abgelehnt
  werden (gilt auch im Simulator)
- Rezeptsuche sowie Filter **Manuell pflegen**
- Mehr als eine Seite Rezepte: bis zum Listenende scrollen und prüfen, dass
  nachgeladen wird — mit und ohne aktiven Filter
- Rezept ohne Zutaten
- Rezept ohne Zubereitungsschritte
- TikTok-/Original-Link öffnet Safari; kein Medium spielt in der App
- Zutaten und Schritte ergänzen, speichern und neu laden
- Favorit, Teilen und **Zur Einkaufsliste**
- Einkauf abhaken, hinzufügen und löschen
- Wochen wechseln, Gericht einplanen, Portionen ändern und Wocheneinkauf erstellen
- Linkimport und manuelle Eingänge in **Verwalten**
- Dynamic Type, Dark Mode und VoiceOver
- Flugmodus, langsames Netz und Serverfehler

## 3. Echtes iPhone

- Automatische Signierung mit dem richtigen Apple-Team aktivieren.
- App direkt aus Xcode installieren.
- HTTPS-Verbindung außerhalb des lokalen WLANs testen.
- Externe Links, Teilen und Schlüsselbund-Wiederanmeldung testen.
- App beenden, Gerät sperren, erneut öffnen und Sitzung prüfen.

## 4. TestFlight

- Ohne Mac auf GitHub **Actions > Native iOS > Run workflow** öffnen.
- **Signierten Build zu TestFlight hochladen** aktivieren und starten.
- Den verarbeiteten Build in App Store Connect zunächst intern verteilen.
- Mindestens einen vollständigen Testlauf auf einem zweiten iPhone durchführen.
- Abstürze, Feedback und App-Hang-Berichte in App Store Connect prüfen.

## 5. Vor dem Review

- Datenschutz- und Support-URL in App Store Connect eintragen.
- App-Icon, Screenshots, Beschreibung und Altersfreigabe prüfen.
- Dem Review-Team ein funktionsfähiges Testkonto und klare Anmeldehinweise geben.
- Bestätigen, dass Rechte und Nutzungsbedingungen der verlinkten Drittanbieter
  eingehalten werden.
