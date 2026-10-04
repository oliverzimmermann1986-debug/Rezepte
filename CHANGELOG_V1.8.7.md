# 1.8.7 – Fachlogik und KI-Belege

- `auth_disabled` schaltet die Anmeldung ausschließlich als echter Boolean
  ab. Die Config-API weist Strings und andere Typen zurück.
- Direkte pytest-Aufrufe und der isolierte Runner verwenden vor dem App-Import
  ausschließlich temporäre Pfade; Mail, externe Datenträger, KI und automatische
  Bild-/Videoanalyse sind in der Testkonfiguration deaktiviert.
- PDF-Zutaten erkennen Dezimalzahlen, deutsche Tausender, einfache und gemischte
  Brüche, Unicode-Brüche und Bereiche. Aufzählungen benötigen einen echten Trenner.
- KI-Zutaten werden lokal gegen Namen, Mengen und Quellbelege geprüft. Unbelegte,
  unvollständige oder unsichere Ergebnisse erhalten `error` zur manuellen Pflege;
  eine vorhandene lokale PDF-Liste bleibt erhalten. Text-/Vision-Antworten ohne
  regulären Abschluss sowie teilweise gelesene Scans werden verworfen. Rasterung
  für PDF-Vision ist auf 1600 Pixel Kantenlänge begrenzt.
- Alle kostenpflichtigen KI-Transportversuche haben persistente serverweite
  Kontingente über 24 Stunden, einschließlich Retries: standardmäßig 1000 Aufrufe
  insgesamt und 40 Bildversuche. Null sperrt weitere Versuche. Diese Zähler ersetzen
  keine monetäre Grenze beim Anbieter. Schema 264 ergänzt die Zählertabelle.
- Suchphrasen behalten ihre Leerzeichen und Reihenfolge; Ausschlüsse matchen
  Wörter/Zutaten statt Teilwörter wie `Ei` in `Reis` oder `Weißwein`.
- Neue Mengen öffnen schon abgehakte Einkaufspositionen wieder. Bekannte
  Zubereitungszusätze werden vereinheitlicht; Rote Bete bleibt als Produkt erhalten.
  Gepflegte Zuordnungen und Ausschlüsse bleiben erhalten; die bestehende
  Synonym-Normalisierung im Wochenplan bleibt wirksam.
  Tomatenmark und Teelicht werden korrekt kategorisiert. Volumen wird in ml/l angezeigt.
- Automatische Bildaufträge erhalten vorhandene Quellcover. Explizite Bildgenerierung
  und der administrative Backfill sichern das Original vor dem Ersetzen. Web,
  SwiftUI- und Expo-Rezeptdetails kennzeichnen ein erfolgreich generiertes Bild.
- Erkennbare Mail-Signaturen werden aus dem KI-Hinweistext entfernt. Datenschutzhinweise
  beschreiben den tatsächlichen lokalen Medienabruf sowie Bild-/Audioübermittlung
  und mögliche Verarbeitung in den USA, abhängig von Anbieter und Projektregion.
- Öffentliche Health-/Readiness-Fehler und HTTP-Fehler ab 500 liefern allgemeine
  Fehlertexte. Interne Pfade/Diagnosen bleiben in den Serverlogs.

Native Quellcodeänderungen benötigen weiterhin einen macOS-/Xcode-Build und
Geräteprüfung. Bruchteile beim Kochen werden bewusst nicht aufgerundet: eine
Portionsskalierung darf das Verhältnis eines Rezepts nicht still verändern.
