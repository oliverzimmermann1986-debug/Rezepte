# Übernahmebericht – Rezeptliebe UI

## Ziel

Das bestätigte Butter-Yellow-/Mobile-First-Design wurde in das richtige `Rezepte-main`-Repository übertragen, ohne dessen bestehende Rezeptfunktionen zu ersetzen.

## Erhaltene Funktionen

- Rezeptsuche und Zutatenfilter
- Favoriten und Bewertungen
- Einkaufsliste
- Rezeptdetails, Zutatenbearbeitung und Kochmodus
- Importprüfung, Historie, Stammdaten und Qualitätsprüfung
- lokale SQLite-Sicherungen und Wiederherstellung
- PWA- und Offline-Funktionen

## Änderungen

- Rezeptbibliothek ist die Startseite.
- Favoriten und Einkaufsliste sind Hauptnavigation.
- Ein einheitliches Stylesheet: `app/static/rezeptliebe.css`.
- Alte Theme-Umschaltung und das alte `style.css` wurden entfernt.
- Desktop: feste warme Sidebar und dreispaltige Rezeptkarten.
- Mobile: kompakte Rezeptkarten, App-Bar und feste Bottom-Navigation.
- Der Inhalt reserviert die volle Footer-Höhe inklusive Safe Area; keine Karte liegt unter der Navigation.
- Erweiterte Filter öffnen als Side-Sheet beziehungsweise mobiles Bottom-Sheet.
- Neue Butter-Yellow-PWA-Icons und aktualisiertes Manifest.
- Entfernte Remote-Sync-Reste wurden aus Runtime, Konfiguration und systemd-Sudoers bereinigt.
- Versteckte Audit-Bereiche starten mit sicheren Defaultwerten und blockieren die Rezeptseite nicht mehr.

## Validierung

- 33 Pytest-Tests
- Python-Kompilierung
- JavaScript-Syntaxprüfung
- Shell-Syntaxprüfung
- `pip check`
- visuelle Browserprüfung mit repräsentativen Rezeptdaten bei 1440 px, 390 px und 360 px
- kein horizontaler Überlauf
- Abstand zwischen letzter mobiler Rezeptkarte und Bottom-Navigation: mindestens 80 px
