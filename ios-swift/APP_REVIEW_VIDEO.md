# App-Review-Video mit Codemagic

Der Workflow `ios-review-video` in `codemagic.yaml` erzeugt zwei Artefakte aus
demselben SwiftUI-Stand:

- `Rezepte.app` als unsignierte Simulator-App für Codemagic App Preview
- `Rezeptregal-App-Review-1.2.0.mp4` als automatisierten Review-Rundgang

## Einmalige Codemagic-Konfiguration

1. Das GitHub-Repository in Codemagic verbinden und die YAML-Konfiguration
   aktivieren.
2. Eine Environment-Variable `APP_REVIEW_PASSWORD` als **Secure** in der Gruppe
   `app_review` anlegen. Das Passwort darf nicht in Git gespeichert werden.
3. In Codemagic **App Preview** für das Team aktivieren. Die erzeugte
   `Rezepte.app` ist danach über **Quick Launch** im Browser startbar.

## Aufnahme starten

Der Workflow wird ausschließlich durch Tags mit dem Präfix `review-video-`
gestartet. Beispiel: `review-video-1.2.0-2302`.

Die UI-Aufnahme verwendet den isolierten Review-Server und zeigt Anmeldung,
Rezeptpass mit Rezept-ID, Originalquelle, Wochenplan, aktuelle und
wiederkehrende Einkäufe sowie die Admin-Einstellungen. Das Passwort wird nur
als geschützte Codemagic-Variable an den UI-Test übergeben und im Video maskiert.

Vor jedem Lauf muss der Review-Datensatz über den dokumentierten
`refresh_app_review_demo.py`-Ablauf geprüft beziehungsweise aufgefrischt werden.
