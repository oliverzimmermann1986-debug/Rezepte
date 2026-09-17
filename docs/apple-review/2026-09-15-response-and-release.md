# Rezeptregal – Review-Korrektur vom 15. September 2026

Status: Umsetzung und Prüfungen; keine Veröffentlichung, kein Upload,
keine Antwort an Apple und keine erneute Einreichung durch diese Änderungen.

## Geschäftsmodell: Antwort noch gesperrt

Betreiberangabe: Alle sollen lesen und angemeldet Bewertungen schreiben
können; normale Nutzer sollen nicht importieren. Gastlesen ist bereits im
eingereichten Build vorhanden. Die Korrektur schließt die bisher offenen
Importendpunkte für normale Konten und passt den nativen Eingang an.
Bewertungen bleiben gemeinsame Werte am Rezept, keine persönlichen Datenreihen.

Vor dem Absenden muss der Betreiber die offenen Antworten vervollständigen:

1. **Who are the users that will use the paid features in the app?**
   Noch offen: Gibt es überhaupt kostenpflichtige App-, Server- oder
   Kontofunktionen? Ohne Bestätigung nicht „no paid features“ behaupten.
2. **Where can users purchase the features that can be accessed in the app?**
   Noch offen: Verkauf/Bezahlung innerhalb oder außerhalb der App, auch für
   Serverzugang. Falls keine Käufe existieren, dies ausdrücklich bestätigen.
3. **What specific types of previously purchased features can a user access?**
   Noch offen: Gibt es extern gekaufte Leistungen, die durch Anmeldung nutzbar
   werden? Falls nein, ausdrücklich bestätigen.
4. **What paid content, subscriptions, or features are unlocked without IAP?**
   Noch offen: Kostenmodell, Abos und externe Freischaltungen. Rollenrechte
   allein sind keine kostenpflichtigen Freischaltungen.
5. **How do users obtain an account? Do users pay to create one?**
   Quellcode: Der Serveradministrator erstellt Konten; kein Signup in der App.
   Gastlesen benötigt kein Konto. Noch offen: tatsächlicher Kontakt-/Anfrageweg
   für neue Nutzer sowie mögliche Gebühren.

Erst nach diesen Angaben eine vollständige englische Antwort formulieren.
Supportfall `102962789398` gehört zur Statusnachfrage vom 14. September;
die inhaltliche Antwort gehört zur App-Review-Einreichung.

## Support veröffentlichen

1. Aktualisierte Betreiberentscheidung: Zimlab-Supportportal mit Formular,
   keine öffentlich sichtbare Empfängeradresse. `support-portal/` enthält die
   separate Website mit D1, Turnstile und einer Owner-only-Verwaltung.
   Die freigegebene Zieldomain ist die bestehende Domain `support.zimlab.org`.
   Das ist noch kein Nachweis einer anonym öffentlichen, einsatzbereiten Seite.
2. Erst nach verifizierter Freischaltung des Portals
   `REZEPTREGAL_SUPPORT_PORTAL_URL=https://support.zimlab.org/?module=rezeptregal`
   setzen. `/support` leitet ausschließlich auf dieses erlaubte Ziel weiter;
   ohne Konfiguration oder bei abweichender URL bleibt die Route mit HTTP 503 gesperrt.
   `REZEPTREGAL_SUPPORT_EMAIL` wird ausdrücklich nicht mehr ausgewertet.
3. Nach ausdrücklicher Deploymentfreigabe den Korrekturstand deployen und den
   Webdienst kontrolliert neu starten. Keine privaten Rezeptdaten veröffentlichen
   und keine globale Anmeldung oder Cloudflare-Schutzrichtlinie deaktivieren.
4. Das Portal, Formular und `/api/public/*` bleiben öffentlich; die eigene
   Access-Anwendung schützt `/admin` inklusive aller Unterpfade. Der Server
   validiert Access-JWT, Issuer, Audience und Owner-E-Mail unabhängig davon.
   Bestehende Schutzregeln der Rezept-App nicht aufheben.
5. Freigegebener Zielwert für das App-Store-Feld **Support-URL** (alle
   Lokalisierungen) und die Repositoryvariable `REZEPTREGAL_SUPPORT_URL`:
   `https://support.zimlab.org/?module=rezeptregal`.
   SwiftUI und Expo verwenden diese Adresse vor und nach der Anmeldung,
   unabhängig vom eingetragenen Rezeptserver und ohne dessen Zugangsdaten.
   Diese Quelländerung aktualisiert weder installierte Apps noch Store-Metadaten.
6. Vor Release anonym prüfen:
   `python tools/check_support_url.py "https://support.zimlab.org/?module=rezeptregal"`.
   Der Check verlangt das kanonische Ziel ohne Anmeldungs-/Fremdhost-Weiterleitung,
   das Kontaktformular, Datenschutz und Impressum sowie ein einsatzbereites Rezeptregal-Modul.
   Zusätzlich eine echte Formulareinsendung und Sichtbarkeit in der geschützten
   Verwaltung prüfen. Der Check liest nur HTML und Formularbereitschaft sowie
   das aktivierte Rezeptregal-Modul; er beweist keine erfolgreiche Einsendung.
   Das Portal verschickt keine automatische E-Mail; Antworten erfolgen aus
   dem eigenen E-Mail-Programm des Betreibers.
7. Erst nach bestandenem Freigabegate den oben genannten Wert tatsächlich in
   allen App-Store-Lokalisierungen und als Repositoryvariable speichern.
   Die vorliegende Änderung ist nur der lokale Metadatenentwurf.

## iOS-Kandidat prüfen und einreichen

- Marketing-Version bleibt `1.2.0`. Bei freigegebenem Upload eine noch nicht
  verwendete höhere Buildnummer als `2307` wählen; vorher Apple-Builds prüfen.
- CI ohne Upload führt XCTest und die Identitätsprüfung des gebauten
  Simulator-Bundles aus. Windows-Quelltests ersetzen keinen Xcode-Build.
- Upload nur mit `upload_testflight=true`. Der Job prüft die öffentliche
  Support-URL, Namen/Bündelkennungen im signierten Archiv und in der finalen IPA.
  Tester werden nicht automatisch zugewiesen.
- Auf einem installierten Kandidaten Homescreen-Name, Share-Menü, Gastlesen,
  normale Benutzerrechte, Adminimport und Support vor/nach Login prüfen.
- Erst danach Build in App Store Connect ersetzen, Supportmetadaten speichern,
  alle fünf Fragen beantworten und ausdrücklich zur Prüfung einreichen.
  „Bereit zur Übermittlung“ ist keine tatsächlich gesendete Einreichung.

## Veröffentlichungsgrenze

Das GitHub-Repository ist laut Live-Abfrage am 15. September öffentlich.
Ein Push würde auch diesen Korrekturstand veröffentlichen. Deshalb bisher
kein Push und kein macOS-CI-Lauf auf diesem Kandidaten. Für den echten
Xcode-Testlauf ist entweder die Freigabe zur Veröffentlichung des Codebranches
oder eine autorisierte private macOS-Buildumgebung erforderlich.

## Technischer Antwortbaustein – erst nach den jeweiligen Nachweisen

The app and its share extension now consistently use the Rezeptregal name,
and the bundle identifiers have not changed. We have updated the Support URL
to a publicly accessible page with direct contact information, available
without an app or GitHub account. Please see the five business-model answers
below.

Nicht sendefertig: erst nach Upload/Metadatenkorrektur und zusammen mit den
bestätigten fünf Antworten verwenden.
