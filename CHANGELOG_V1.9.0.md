# 1.9.0 – Konten und Anmeldewege

- Eigene Kontoseite mit Passwortänderung, einzelnen Gerätesitzungen, Abmeldung auf
  allen Geräten und bestätigter Kontolöschung in Web, SwiftUI und Expo.
- Benutzerverwaltung für Administratoren: Anlegen, Rollen, Sperren, Passwortreset,
  Löschen und Widerruf aller Sitzungen eines Kontos.
- Optionale Apple-/Google-Anmeldung und ausdrückliche Kontoverknüpfung. Anbieter
  erscheinen erst nach serverseitiger Einrichtung. Gleiche E-Mail-Adressen führen
  nicht zu einer automatischen Kontoverknüpfung.
- Einzelne Sitzungen werden serverseitig widerrufen; lokale Abmeldung bleibt auch
  bei einem fehlgeschlagenen Löschen aus dem Gerätespeicher wirksam.
- Schema 267/268 ergänzt Sitzungen, Anbieteridentitäten und einmalige
  Anmeldevorgänge. Nach dem Upgrade ist einmaliges erneutes Anmelden erforderlich.
  Bestehende Konten, Passwörter und Haushalte bleiben erhalten.
- Neue Passwörter benötigen mindestens zehn Zeichen und maximal 72 UTF-8-Bytes.
  Der letzte aktive Administrator und bestehende Haushaltsdaten bleiben geschützt.

Einrichtung und Grenzen: [Kontoverwaltung](docs/account-management.md).
