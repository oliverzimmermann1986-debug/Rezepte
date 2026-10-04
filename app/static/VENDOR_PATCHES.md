# Lokales Alpine.js

Basis: mitgelieferte Alpine-Version 3.13.5; weiterhin vollständig lokal.

03.10.2026: In der Hide-Kaskade wurde `.then(([u])=>u())` durch
`.then(([u])=>u?.())` ersetzt. Bei schnellen, verschachtelten `x-show`-Wechseln
kann ein bereits konsumiertes Hide-Promise keinen Callback mehr liefern.
Der unbedingte Aufruf führte dann zu `TypeError: u is not a function`.

Die Änderung übernimmt die entsprechende Absicherung aus der
[Alpine-Implementierung von x-transition](https://github.com/alpinejs/alpine/blob/main/packages/alpinejs/src/directives/x-transition.js).
Sie aktualisiert die Bibliothek nicht pauschal. Ein späterer Vendor-Austausch
muss diese Absicherung und den schnellen Wochenplan-/Einkaufsablauf aus
`tests/test_web_browser.py` erhalten. Der Vendor ist in den gemeinsamen
Cache-Buster aufgenommen.
