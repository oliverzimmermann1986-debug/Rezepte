# Rezeptregal 1.3 – Vorbereitung der nächsten Prüfung

Dies ist der Review-Entwurf für den nächsten Build, keine Bestätigung einer
Einreichung oder Freigabe. Die laufende Übermittlung von 1.2.0 bleibt unverändert.
Vor Verwendung müssen die unten beschriebenen Abläufe mit genau dem Kandidaten
und dem angegebenen Review-Server erfolgreich aufgenommen werden.

## Produktgeschichte

**Aus einer gespeicherten Quelle wird dein bewährtes Rezept.** Rezeptregal
verbindet Quellenimport, nachvollziehbare Korrekturen und persönliche
Kocherfahrungen. Geöffnete Rezepte und der eigene Kochfortschritt sind lokal
verfügbar. Der iPhone-Client setzt für Anmeldung, Import und Synchronisierung
einen kompatiblen HTTPS-Rezeptserver voraus; er ist kein öffentlicher
Rezeptmarktplatz und bietet keine Selbstregistrierung für einen Cloud-Dienst.

Die nächste Aufnahme zeigt zuerst diese drei Abläufe:

1. **Import nacharbeiten:** ein Rezept öffnen, die strukturellen Hinweise und
   die erhaltene Originalquelle prüfen, Zutaten und Schritte korrigieren.
   Administratoren übernehmen bestätigte Änderungen unmittelbar; reguläre
   Konten reichen einen Korrekturvorschlag zur Freigabe ein.
2. **Beim nächsten Kochen besser werden:** persönliche Notizen, Änderungen und
   einen Tipp fürs nächste Mal am Rezept erfassen. Die Einträge gehören zum
   angemeldeten Konto und sind von gemeinschaftlichen Rezeptdaten getrennt.
3. **In der Küche weiterkochen:** im Archiv das „Offline-Regal“
   öffnen und ein bereits geladenes Rezept sowie den gespeicherten
   Kochfortschritt wieder aufrufen. Bilder und verlinkte Originalseiten können
   weiterhin eine Internetverbindung benötigen.

Einkauf, Wochenplanung, Quellenvergleich und kuratierte Zutatenvarianten bleiben
zusätzliche bestehende Funktionen. Der Menü-Dirigent wird in 1.3 nicht erweitert
und nicht als neue Kernfunktion beworben.

## Voraussetzungen für den Review-Zugang

- Isolierter Review-Server mit künstlichen Rezeptdaten und passendem Backend.
- `/api/system/info` muss `cooking-memory-v1` und `import-review-v1` melden.
- Das Review-Konto erhält nur Zugriff auf diese künstliche Instanz.
- Zugangsdaten werden ausschließlich in den geschützten App-Review-Feldern
  hinterlegt. Das optionale Cloudflare-Feld bleibt für diese Instanz leer.
- Die Tabs des Kandidaten lauten **Eingang, Archiv, Heute, Einkauf,
  Einstellungen**. Der Gastzugang zeigt ein eingeschränktes Archiv und benötigt
  ebenfalls eine Serveradresse; persönliche Notizen und Schreibaktionen werden
  mit einem angemeldeten Konto geprüft.

Der Quellenvergleich bewertet beobachteten Quelltext und strukturelle Hinweise.
Er bestätigt weder Richtigkeit eines Rezepts noch Lebensmittel- oder
Allergensicherheit. Substitutionsvorschläge sind kuratiert, nicht universell;
Zubereitung und Produktetiketten erfordern weiterhin eigene Prüfung.

## Englischer Text für App Review

Der konkrete englische Rundgang steht in
[`review-demo/APP_REVIEW_NOTES.en.md`](../review-demo/APP_REVIEW_NOTES.en.md).
Er darf erst nach bestandenem Kandidaten-Rundgang übernommen werden. Eine
native Implementierung oder neue Funktionen garantieren keine Annahme durch
App Review; die Produktunterschiede werden anhand realer Abläufe gezeigt.

## Nachweise vor der nächsten Einreichung

Die Reihenfolge und offenen Gates stehen in
[`APP_STORE_RELEASE_CHECKLIST.md`](APP_STORE_RELEASE_CHECKLIST.md). Neue
Screenshots und ein separates Review-Video entstehen mit
[`APP_REVIEW_VIDEO.md`](APP_REVIEW_VIDEO.md). Alte Aufnahmen mit
„Rezepte / Wochenplan / Einkauf / Admin“ dürfen nicht wiederverwendet werden.
