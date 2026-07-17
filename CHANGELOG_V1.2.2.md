# Rezepte v1.2.2

## Admin Center für alle Benutzer

- Admin-Rollen und Rollenprüfungen wurden entfernt.
- Jeder aktive, angemeldete Benutzer hat vollständigen Zugriff auf das Admin Center.
- Der Admin-Einstieg ist auf Desktop und Handy immer sichtbar.
- `/admin` und `/admin/pdf` liefern die Anwendung direkt aus.
- Benutzerkonten steuern nur noch Login, Passwort und Aktivstatus.
- Rollenwahl, Rollenwechsel, Lockout-Logik und der CLI-Befehl `user-role` wurden entfernt.
- Der PWA-Service-Worker lädt JavaScript und CSS network-first und verwendet einen neuen Cache-Namespace.

Nach dem Update den Dienst neu starten und eine installierte PWA einmal vollständig schließen und erneut öffnen.
