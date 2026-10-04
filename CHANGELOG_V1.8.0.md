# Version 1.8.0

Haushalte haben getrennte private Rezepte, Sammlungen, Bewertungen, Favoriten,
Einkaufslisten und Wochenpläne. Zwei persönliche Anmeldungen können per Einladung
denselben Haushalt verwenden. Gäste lesen die globale Rezeptbibliothek.

Private URL-Importe verlinken bereits vorhandene globale Rezepte. Sie starten
dafür keinen Download und keinen KI-Lauf. Web und TypeScript-iOS bieten die
Ansichten Alle, Global und Mein Haushalt sowie das Merken und Entfernen globaler
Rezepte.

Die Migration auf Schema 261 übernimmt bestehende Rezepte als globale Sammlung
und die bisherigen persönlichen Daten einmalig in den Betreiberhaushalt. Die
Funktionen von Version 1.7.0 bleiben verfügbar. Quellenstände, Varianten,
Zutaten-Hints, Einkaufsvorschläge und Wartung beachten Haushaltsgrenzen.

Lokale Updates prüfen Datenbankschema und vorhandene Fähigkeiten vorab. Ein
Fehler stellt Code, Datenbank, Konfiguration und installierte Dienste zusammen
wieder her. Ausgeschaltete Timer und die Isolation der Review-Instanz bleiben
erhalten.
