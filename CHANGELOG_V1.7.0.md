# Rezepte 1.7.0 / Quellenküche 1.2.0

## Neu

- Der **Menü-Dirigent** plant mehrere Gerichte rückwärts zu einer gemeinsamen
  Servierzeit. Herd, Ofen und aktive Köchinnen oder Köche werden als begrenzte
  Ressourcen berücksichtigt.
- Der **Quellenwächter v2** zeigt einen vollständigen, sicherheitsorientierten
  Vergleich zur gespeicherten Quelle. Ein geprüfter Snapshot wird nur noch
  übernommen, wenn er sich seit der Anzeige nicht verändert hat.
- Das **Substitutionslabor** zeigt konkrete Vorher-/Nachher-Zutaten,
  Mengenverhältnisse, Einschränkungen und mögliche Folgen. Das Original bleibt
  unverändert; eine Übernahme erzeugt eine nachvollziehbare Variante.

## Native App

- Originalquelle und Rezept-ID sind im Rezeptpass sichtbar.
- Wiederkehrende Einkäufe und die vollständigen administrativen Einstellungen
  stehen in der nativen SwiftUI-App zur Verfügung.
- Varianten tragen ihre Herkunft und einen dauerhaften Prüfhinweis. Kritische
  Aktionen zeigen Fortschritt und können nicht unbemerkt im Hintergrund
  weiterlaufen.

## Sicherheit und Betrieb

- Unfertige Varianten bleiben nach Abbruch unsichtbar, werden atomar
  veröffentlicht oder zurückgerollt und können keine veralteten Nährwerte
  übernehmen.
- Deployment-Gates prüfen Version, alle nativen Capabilities und die exakten
  OpenAPI-Methoden der neuen Funktionen.
- Der SwiftUI-Release prüft Bundle-ID, Marketing- und Buildversion und wartet
  nach dem Upload nur auf genau den neuen Build desselben Versionszugs; ein
  älterer Build mit wiederverwendeter Nummer kann das Gate nicht erfüllen.
- Die isolierte App-Review-Instanz repariert ihren Schutzmarker selbst und hält
  den Importtimer nach Updates deaktiviert. Bestehende künstliche Review-Daten
  werden nach einem geprüften SQLite-Backup atomar und wiederholbar auf den
  dokumentierten Quellen-, Zwölf-Wochen-Plan- und Einkaufsstand angehoben;
  dadurch bleiben Menü-Dirigent, Warenkorb und wiederkehrender Einkauf auch nach
  einem Wochenwechsel sofort prüfbar. Benutzerkonten, Zugangsdaten und lokale
  Produktstatistiken werden nicht verändert.
- Der Deployment-Health-Poll verwirft alte Antwortdateien und verlangt einen
  explizit erfolgreichen neuen Abruf, bevor die Versionsprüfung beginnt.
