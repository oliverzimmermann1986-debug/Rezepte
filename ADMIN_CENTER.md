# Admin-Zentrale – Rezepte 1.2.2

## Zugang zum Admin Center

- Im Browser direkt `/admin` öffnen.
- Für die PDF-Verarbeitung direkt `/admin/pdf` öffnen.
- Auf Mobilgeräten ist das Werkzeug-Symbol immer in der Kopfzeile sichtbar.
- Jeder aktive, angemeldete Benutzer hat vollständigen Zugriff.
- Benutzerkonten steuern nur Anmeldung, Passwort und Aktivstatus; Admin-Rollen gibt es nicht mehr.

Der Admin-Reiter bündelt alle technischen und qualitätssichernden Werkzeuge. Die reguläre Rezeptsuche, Favoriten und Einkaufsliste bleiben davon getrennt.

## Importzentrale

Die Importzentrale verdichtet Pending-Einträge, fehlgeschlagene Downloads, laufende Jobs und den letzten Verlauf. Dadurch lässt sich der Zustand der Importpipeline an einer Stelle prüfen, ohne zwischen mehreren technischen Seiten zu wechseln.

## Rezeptversionen und Rückgängig

Vor inhaltlichen Änderungen speichert Rezepte einen vollständigen Snapshot der strukturierten Rezeptdaten:

- Name, Typ, Kategorie und Beschreibung
- Portionen und Nährwerte
- Zutaten
- Zubereitungsschritte
- Tags und Qualitätsstatus

Die Detailansicht zeigt Feldänderungen sowie hinzugefügte oder entfernte Zutaten und Tags. Eine Version kann atomar wiederhergestellt werden; davor entsteht automatisch ein neuer Snapshot als Rücksprungpunkt. Favoriten und persönliche Bewertungen bleiben beim Restore unverändert.

Binärmedien werden nicht in jeder Rezeptversion dupliziert. PDF-Bearbeitungen sichern stattdessen das konkrete Original separat.

## Intelligente Suche

Die Suche unterstützt:

- lokal gepflegte Synonymgruppen
- Unicode- und Umlaut-normalisierte Vergleiche
- Teiltreffer in zusammengesetzten Begriffen
- Ausschlüsse mit `-Zutat` oder `ohne Zutat`
- transparente Tippfehlerkorrektur mit sichtbarem Hinweis
- gewichtete Relevanzbewertung

Beispiele:

```text
Kartoffel Pfanne
Hack ohne Zwiebel
Tomate -Sahne
```

Synonyme können im Admin-Reiter ergänzt, geändert oder gelöscht werden. Der Suchindex lässt sich dort ebenfalls kontrolliert neu aufbauen.

## PDF & Scan

Der Admin bietet eine Stapelverarbeitung und einen manuellen Seiteneditor. Details stehen in `PDF_PROCESSING.md`.

## Wartung

Jeder Wartungslauf wird mit Benutzer, Start-/Endzeit, Ergebnis und Details protokolliert. Verfügbar sind:

- SQLite-Integritäts- und Fremdschlüsselprüfung
- verifiziertes Testbackup; nur die fünf neuesten Prüfbackups bleiben erhalten
- Medien- und Pfadprüfung
- Bereinigung alter temporärer Dateien
- Neuaufbau des Volltextindex
- SQLite `VACUUM`

## Technische Aufteilung

Die neuen Funktionen liegen bewusst nicht im bisherigen Monolithen:

- `app/routes/api_admin.py` – Admin-API und Wartung
- `app/core/pdf_processing.py` – PDF-/Scan-Verarbeitung
- `app/recipes/search.py` – Suchplanung, Synonyme und Vorschläge
- `app/db.py` – atomare Snapshots, Migrationen und Wartungsprotokolle

Diese Trennung reduziert Seiteneffekte und macht die kritischen Funktionen separat testbar.

## PDF-Lauf schlägt fehl

Seit v1.2.3 zeigt der PDF-Reiter vor dem Start eine Systemprüfung und anschließend den konkreten Fehler je Datei. Bestandsläufe laufen im Hintergrund und überstehen einen geschlossenen Browser-Tab oder einen Proxy-Timeout.

Diagnose im Container:

```bash
sudo -u scrapper /opt/scrapper/venv/bin/python -m app.cli pdf-doctor
journalctl -u scrapper-web -n 200 --no-pager
```

## PDF-Rezeptdaten auslesen (v1.2.5)

Im Bereich **PDF & Scan** kann neben der Bildverbesserung auch die strukturierte Rezeptauswertung aktiviert werden. Der Lauf liest Zutaten, Mengen, Einheiten, Schritte, Portionen und Tags aus neuen oder bestehenden PDF-Dateien. Bereits gepflegte Daten werden standardmäßig geschützt; ein bewusstes Überschreiben legt vorher eine Rezeptversion an.
