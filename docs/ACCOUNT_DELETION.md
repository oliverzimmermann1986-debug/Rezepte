# Dauerhafte Löschung des eigenen Haushalts

`DELETE /api/account/profile` behält die bisherige Kontolöschung bei. Mit `delete_household: true` und `confirmation: "HAUSHALT LÖSCHEN"` kann das einzige Mitglied zusätzlich den eigenen privaten Haushalt löschen. Der Server akzeptiert keine vom Client gewählte Haushalts-ID. Ein Passwort beziehungsweise eine frische Anbieterbestätigung ist weiterhin erforderlich.

Unter Haushalts-, Rezept- und Bildsperren sowie einer `BEGIN IMMEDIATE`-Transaktion werden Sitzungsversion, Eigentümer, Mitgliedschaft und Alleinmitgliedschaft erneut geprüft. Der letzte aktive Serveradministrator bleibt geschützt. Ein zweites Mitglied kann nicht durch einen veralteten Bildschirm mitgelöscht werden. Laufende Importe und beanspruchte Analyse-/Bildaufträge ergeben HTTP 409; wartende eigene Aufträge werden atomar entfernt.

Die Transaktion entfernt private Rezept- und Haushaltsdaten einschließlich Versionen, Kochfotos, Freigaben, Einladungen, Sitzungen und zugeordneter Gerätezugänge. Globale Rezepte und andere Haushalte bleiben erhalten. Anbieter-Widerrufe verwenden die vorhandene persistente Widerrufsliste; dafür notwendige verschlüsselte Tokens bleiben bis zum erfolgreichen Widerruf bestehen.

## Dateien und Recovery

Schema 272 ergänzt `household_purges` und `deleted_households`. Das Dateimanifest wird zusammen mit der Kontolöschung gespeichert, bevor Dateien entfernt werden. Es enthält nur vorab zugeordnete Ziele unter den konfigurierten Rezept-, temporären, Papierkorb- und Bildoriginalverzeichnissen. Fremde Überschneidungen, Verzeichniswurzeln sowie Symlink-/Junction-Ziele werden abgelehnt. Verbleibende historische Tombstones enthalten nur Haushalts-ID und Löschzeit, um verspätete Datenbank-Schreibversuche abzuweisen.

Nach erfolgreicher Dateibereinigung antwortet die API mit HTTP 200 und `status: "deleted"`. Bei einem Dateifehler antwortet sie mit HTTP 202 und `status: "deletion_pending"`: Die Anmeldung ist bereits entfernt; die Dateioperation ist ausdrücklich noch offen. Das gespeicherte Manifest bleibt bis zum Abschluss erhalten. Es gibt keine Erfolgsmeldung für noch ausstehende Dateibereinigung.

`app.account_deletion.recover_purges(db, limit=10)` wird beim Dienststart und periodisch aufgerufen. Fehlgeschlagene Aufträge rotieren nach dem letzten Versuch, damit dauerhaft blockierte Dateien andere Haushalte nicht aufhalten. Pro Auftrag verhindert ein Prozesslock parallele Bereinigung. Operatoren können die Tabelle `household_purges` auf ausstehende Einträge prüfen. Pfade und Rohfehlermeldungen erscheinen nicht in öffentlichen API-Fehlern.

## Grenzen alter Daten

Neue Rezeptarchive tragen den expliziten Haushaltseigentümer; bei einem Haushaltsbeitritt wird diese Zuordnung mitgeführt. Alte Archive lassen sich anhand noch vorhandener Rezeptzuordnung oder eines eindeutig eigenen Haushaltsverzeichnisses erfassen. Bereits früher verschobene und hart gelöschte Archive ohne eindeutige Besitzerzuordnung werden konservativ erhalten. Solche Altbestände benötigen eine Prüfung durch den Betreiber; fremde Daten werden nicht anhand eines Namens geraten.

Diese Bereinigung betrifft die aktiven Anwendungsdaten und eindeutig zugeordnete Dateien. Externe Betreiber-Backups, Exportkopien und Kopien auf fremden Geräten werden dadurch nicht entfernt. Die Umsetzung verspricht keine physische Überschreibung von Datenträgerblöcken oder rückwirkende Löschung aus solchen Sicherungen.

## Gezielte Prüfung

`python tools/run_isolated_tests.py -q tests/test_account_deletion.py tests/test_account_management.py tests/test_household_lifecycle.py tests/test_hardening.py`

`node --test tests/web_account_deletion.test.cjs`

Alle Testdaten sind synthetisch und liegen in der isolierten Testumgebung. Der Symlinktest benötigt auf Windows die entsprechende Betriebssystemberechtigung; ohne sie wird dieser Test ausdrücklich übersprungen.
