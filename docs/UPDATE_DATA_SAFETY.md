# Gespeicherte Daten bei Updates

Ein normales App- oder Serverupdate behält den Haushalt, Wochenplan, Einkauf,
Favoriten, Bewertungen, Wünsche, Kochbücher und Kochfortschritt. Die lokalen
iOS-Einkaufsjournale und Wochenprofile verwenden weiterhin dieselben
kontogebundenen Speicherorte und Formate. Ein Deinstallieren der App ist kein
normales Update und kann lokal noch nicht abgeglichene Änderungen entfernen.

## Serverupdate

`proxmox/update-local.sh` hält Anwendungsdienst und sonstige Schreiber an. Eine
geprüfte SQLite-Sicherung sowie Konfiguration und Diensteinstellungen liegen in
`/opt/scrapper-code-backups/state-*`. Anwendungscode wird getrennt gesichert;
`data`, `files`, `logs` und `temp` werden beim Kopieren des Releases ausgenommen.

Vor dem ersten neuen Dienststart öffnet der neue Code die Datenbank ohne
Hintergrundarbeiter. `tools/release_state.py verify-user-data` vergleicht danach
alle ursprünglichen Spalten und Datensätze aus 38 geschützten Tabellen mit der
Sicherung. Dazu gehören Portionszahlen, Einkaufshaken, Rezeptbezüge,
Wochenverknüpfungen, Offline-Abgleichsbelege, Wiederherstellungseinträge und
Haushaltspräferenzen. Neue Spalten und zusätzliche Zeilen sind zulässig;
ursprüngliche Werte, Zeilen oder Felder dürfen nicht verschwinden. Zusätzlich
werden SQLite-Integrität und Fremdschlüssel geprüft. Das Ergebnis wird im
Sicherungsverzeichnis als `user-data-verification.json` aufbewahrt.

Scheitert diese Prüfung, erfolgt die Wiederherstellung, bevor neue öffentliche
Schreibzugriffe möglich sind. Migrationen mit beabsichtigten Änderungen an
geschützten Werten benötigen eine ausdrücklich geprüfte Erhaltungsstrategie;
die Prüfung darf dafür nicht einfach entfernt werden.

Nach dem ersten Dienststart können andere Geräte bereits neue Daten gespeichert
haben. Bei einem späteren Fehler werden deshalb die Dienste angehalten und die
aktuellen Daten erhalten. Ein älteres Datenbankbackup darf dann erst nach
Prüfung und Übernahme neuer Änderungen manuell zurückgespielt werden.

Die App-Review-Instanz darf anschließend ihre ausdrücklich künstlichen
Demonstrationsdaten aktualisieren. Diese Sonderbehandlung gilt ausschließlich
für die markierte Review-Instanz und nicht für den produktiven Haushalt.
