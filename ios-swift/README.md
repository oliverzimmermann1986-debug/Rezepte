# Quellenküche für iPhone

Primärer nativer iOS-Client in Swift und SwiftUI. Der Eingang für Administratoren übernimmt
Rezeptlinks aus Webseiten, Pinterest, YouTube, TikTok und Instagram sowie
Fotos und PDFs. Videos werden weder geladen noch abgespielt; die Originalquelle
bleibt am Rezept sichtbar. Fehlen Zutaten oder Zubereitungsschritte, bleibt das
Rezept mit einem Hinweis zur manuellen Pflege erhalten.

Das Design ist kein Expo-Template: Navigation, Theme-Persistenz, Dark Mode,
Share Extension und Oberflächen sind native SwiftUI-Komponenten. Die vier
Farbwelten lassen sich unter **Einstellungen** pro Gerät ändern.

Über **Als Gast ansehen** ist kein separates Konto nötig. Der Gast erhält eine
signierte, rein lesende Sitzung und sieht die globalen Rezepte. Einkauf und
Wochenplanung zeigen eine Erklärung mit Anmeldung und Registrierung. Private
Haushaltsdaten, Import, Bearbeitung, Favoriten und Administration bleiben für
Gäste serverseitig gesperrt.

Über **Konto erstellen** lässt sich ein eigener Haushalt registrieren. Unter
**Einstellungen > Mein Konto > Mein Haushalt & Einladungen** kann dessen Eigentümer eine
zweite Person mit eigener Anmeldung einladen. Der einmal verwendbare Link gilt
sieben Tage und lässt sich widerrufen. Ein bestehendes Konto kann denselben Link
dort annehmen; seine privaten Rezepte und Listen gehen in den gemeinsamen Haushalt
über. Der Link wird ausschließlich über die native Teilen-Funktion oder manuelles
Kopieren weitergegeben.

Das Archiv bietet **Alle**, **Global** und **Mein Haushalt**. Globale Rezepte
werden über einen Verweis in der eigenen Sammlung gespeichert. Neue Importe
bleiben standardmäßig privat. Ist ihr normalisierter Link bereits global
vorhanden, speichert der Server den Verweis ohne erneuten Download oder KI-Lauf.
Administratoren können ausdrücklich global importieren. Änderungen an globalen
Rezepten bleiben Administratoren vorbehalten; eigene private Rezepte dürfen
Haushaltsmitglieder bearbeiten.

Normale Konten können über **Eigene Variante erstellen** jedes sichtbare Rezept
privat in ihren Haushalt kopieren und anschließend bearbeiten. Nach dem Anlegen
öffnet sich die neue Variante; das Original bleibt unverändert. Importe, OCR,
KI-Bilder, Neuanalyse, Nährwerte, Übersetzung und KI-Einkaufsoptimierung stehen
nur Administratoren zur Verfügung. Geteilte Links eines normalen Kontos oder
Gasts bleiben mit einem Hinweis auf dem Gerät gespeichert; sie werden weder
automatisch verarbeitet noch beim Abmelden still gelöscht. Einkauf, Text-Export,
Planung, Kontoverwaltung und die manuelle Pflege eigener Rezepte bleiben verfügbar.

Beim Abmelden oder Haushaltswechsel werden die privaten Ansichten neu aufgebaut
und Antwort-Caches geleert. Verspätete Antworten der vorherigen Sitzung werden
verworfen, auch wenn sie erfolgreich sind oder eine abgelaufene Anmeldung melden.

Offene Importe lassen sich nativ vollständig prüfen: Name, Beschreibung,
Portionen, Zutaten, Mengen, Einheiten, Schritte und Timer bleiben editierbar.
Ein Foto-Scan oder eine erneute KI-Analyse aktualisiert den Vorschlag, bevor
die kontrollierte Fassung als Rezept gespeichert wird.

Der Rezeptpass führt direkt in einen ablenkungsarmen Kochmodus. Er skaliert
Zutaten nach Portionen, führt schrittweise durch die Zubereitung, bietet Timer
und speichert den Fortschritt pro Konto. Ein bestätigter Abschluss wird mit
einer stabilen Idempotenz-ID genau einmal in die Kochhistorie eingetragen.

Unter **Quellenwächter & Rezept-TÜV** zeigt der Rezeptpass den gespeicherten
Quellstand, den letzten sicheren Abruf und erkannte Textänderungen als Diff.
Keine Prüfung überschreibt das Rezept automatisch. Erst eine ausdrückliche
Bestätigung verschiebt den Vergleichsanker; Zutaten und Schritte bleiben davon
unberührt. Der Rezept-TÜV ergänzt diesen Nachweis um lokale, deterministische
Hinweise zu Vollständigkeit, Mengen und Dubletten.

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

## Anmeldung und Sitzungen

Die App meldet sich mit Benutzername und Passwort direkt beim Rezepte-Server an.
Registrierung, Haushaltseinladungen und der lesende Gastzugang verwenden dieselbe
App-Authentifizierung. Der Sitzungsschlüssel bleibt im iOS-Schlüsselbund und wird
als Bearer-Token an geschützte API-, Bild- und PDF-Endpunkte gesendet.

**Mein Konto** zeigt Benutzername und Rolle. Dort lassen sich das Passwort ändern
oder einrichten, aktive Sitzungen einzeln oder gemeinsam abmelden und das eigene
Konto löschen. Ein Passwortwechsel meldet alle Geräte ab. Der Server verhindert
das Löschen des letzten Administrators und des letzten Haushaltsmitglieds, wenn
noch Haushaltsdaten oder Importe vorhanden sind. Unter **Administration > Benutzer**
können Administratoren Konten anlegen, Rollen und Aktivstatus ändern, Passwörter
zurücksetzen sowie Sitzungen widerrufen. Benutzernamen werden nicht umbenannt.

Apple und Google erscheinen nur, wenn der ausgewählte Server den Anbieter als
aktiv meldet. Die Anmeldung öffnet `ASWebAuthenticationSession`; Provider-Schlüssel
liegen ausschließlich auf dem Server. Der Client verwendet S256-PKCE und akzeptiert
den Rückruf `de.mausbaeren.rezepte://auth/callback` ausschließlich für den aktuell
gestarteten Flow. Ein einmaliger Code wird anschließend gegen die normale
App-Sitzung getauscht. Dafür werden keine Provider-SDKs oder Apple-Sign-in-Entitlements
eingebunden. Die App Group bleibt der Share-Importqueue vorbehalten.

Unter **Mein Konto > Apple, Google & Anmeldeverfahren** können Konten verknüpft,
erneut bestätigt und entfernt werden; mindestens ein Anmeldeverfahren bleibt
erhalten. Konten ohne Passwort benötigen für geschützte Änderungen eine höchstens
fünf Minuten alte Provider-Bestätigung. Eine erneute Bestätigung baut die
kontogebundenen Ansichten mit der neuen Sitzung auf.
Das erste Verknüpfen eines weiteren Anbieters verlangt das aktuelle Kontopasswort
oder eine frische Provider-Bestätigung. Ein bereits verbundener Anbieter kann
ohne Kontopasswort erneut bestätigt werden. Beim Entfernen einer Verknüpfung
widerruft der Server alle über diesen Anbieter erstellten App-Sitzungen.

Beim normalen Abmelden widerruft die App die aktuelle Serversitzung und entfernt
den lokalen Schlüssel. Eine dauerhaft gespeicherte Abmeldeabsicht verhindert auch
bei fehlgeschlagener Schlüsselbund-Löschung eine automatische Wiederanmeldung.
Bei Netzwerkfehlern ist die lokale Abmeldung möglich; ein erfolgreicher Widerruf
auf dem Server ist dann nicht bestätigt. Frühere Sitzungsschlüssel ohne einzelne
Sitzungskennung benötigen nach dem Serverupdate einmalig eine neue Anmeldung.

Cloudflare Access ist für den Rezepte-Server entfernt. Beim Start und Abmelden
bereinigt die App frühere Gerätezugang-Einträge im Schlüsselbund. Echte Konto- und
Gastsitzungen bleiben bei der Migration erhalten; der alte Platzhalter
`cloudflare-access` wird verworfen und erfordert eine neue App-Anmeldung.

Die getrennte Cloudflare-Konfiguration des externen Einkauf-Dienstes bleibt in
den Admin-Einstellungen verfügbar. Der Rezepte-Server verwendet diese Zugangsdaten
ausschließlich für seine Anfragen an den Einkauf-Dienst.

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
2. Im iPhone-Simulator Registrierung, Gastzugang, Login, Haushaltseinladung,
   Wechsel zwischen zwei Konten, globale Verweise, private Importe und die
   Schreibsperre für globale Rezepte prüfen. Anschließend Quellen-Eingang, Rezeptpass, Quellenwächter,
   Substitutionslabor, Menü-Dirigent, Bildverlauf, Wochenplan, wiederkehrende
   Einkäufe, Admin-Einstellungen, Farbwelten und Einkaufskatalog prüfen. Beim
   Upgrade eine echte gespeicherte Sitzung sowie einen alten
   `cloudflare-access`-Platzhalter prüfen: Die echte Sitzung bleibt nutzbar,
   der Platzhalter führt zur App-Anmeldung ohne Gerätezugang-Felder.
   Zusätzlich Profil, Passwortwechsel, Einzel-/Gesamtabmeldung, Adminbenutzer,
   gesperrte Selbstlöschung sowie Apple-/Google-Abbruch, Verknüpfung und Rückkehr
   aus dem Systembrowser mit aktivierter Testkonfiguration prüfen.
3. Auf einem registrierten iPhone aus Xcode installieren.
4. Über **Product > Archive** einen internen TestFlight-Build hochladen.

Die App enthält keine WebView und keinen Video-Player.
