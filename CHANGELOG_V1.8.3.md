# 1.8.3 — Persönlicher Kochzustand und Kostenkontingente

- Persönlicher Kochfortschritt und Abschlusswiederholungen verwenden feste
  Benutzer-IDs. Ein gelöschter und erneut registrierter Name erbt keine alten
  Zwischenstände oder Abschlüsse. Die Kochhistorie bleibt beim Haushalt.
- Schema 263 ergänzt diese Bindung. Nachvollziehbare alte Einträge werden
  zugeordnet; unklare Altdaten bleiben unverändert gespeichert und unzugeordnet.
- Einzelne Rezeptbilder verwenden dasselbe persistente Kontingent wie Analysen:
  standardmäßig 20 je Haushalt und 200 insgesamt innerhalb gleitender 24 Stunden.
- Aktive Aufträge bleiben ohne zusätzlichen Verbrauch wiederholbar. Endet ein
  Auftrag während der Wiederholung, werden Kontingentprüfung und neuer Auftrag
  in derselben Datenbanktransaktion ausgeführt.
- Haushaltsbeitritt bewahrt den eigenen Kochfortschritt und gültige
  Abschlusswiederholungen. API-Antworten behalten ihr bisheriges Format.
