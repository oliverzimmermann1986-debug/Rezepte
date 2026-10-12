# 1.15.4

- Ein geplanter Rezeptname im iOS-Wochenplan öffnet die Rezeptansicht. Portionswahl, Austausch und Entfernen bleiben separate Aktionen; die gewählte Woche bleibt beim Zurückgehen erhalten.
- Jede Einkaufszutat besitzt eine eigene Info-Schaltfläche. Sie zeigt zugehörige Rezepte und gespeicherte Mengenanteile mit direktem Rezeptaufruf. Manuelle Einträge und nicht mehr verfügbare Rezepte werden benannt; unbekannte Mengen werden nicht geschätzt. Die Info ändert keine Einkaufsdaten und folgt späteren Listenänderungen.
- Die Web-Einkaufsliste bietet dieselbe Rezeptinfo; ihre Schaltflächen sind auf kleinen Bildschirmen getrennt bedienbar.
- Updates migrieren die Datenbank bei angehaltenen Schreibern und vergleichen 38 Tabellen mit der geprüften Sicherung, bevor der neue Dienst startet. Fehlende Zeilen, veränderte gespeicherte Werte und entfernte Felder brechen das Update ab. Neue Felder und zusätzliche Datensätze bleiben möglich.
- Ein Fehler nach dem ersten Dienststart spielt keine alte Datenbank automatisch zurück. Der aktuelle Stand bleibt erhalten und die Dienste werden angehalten, damit neue Änderungen anderer Geräte nicht verschwinden.
- Schema 274 und die vorhandenen lokalen Speicherformate bleiben unverändert.
