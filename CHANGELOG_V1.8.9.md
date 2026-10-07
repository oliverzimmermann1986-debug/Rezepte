# 1.8.9 – Eigene Anmeldung in Web und iOS

Web, SwiftUI und der ältere Expo-Client verwenden ausschließlich die Anmeldung
des Rezeptservers. Die Cloudflare-Gerätezugangsfelder und ausgehenden
Access-Header des Rezeptclients entfallen. Frühere Gerätezugangsdaten werden
aus dem Gerätespeicher entfernt; echte Rezept-Sitzungen bleiben erhalten.
Der alte Ersatzwert `cloudflare-access` ist keine gültige Sitzung mehr.

Die Backend-Rollenprüfung gilt auch hinter vertrauenswürdigen Proxys.
`web.auth_disabled` und `web.external_logout_url` werden aus alten Konfigurationen
ignoriert und beim Speichern entfernt. Logout widerruft die eigene Sitzung und
führt zur Anmeldung. Bestehende Konten, Kennwörter und Haushalte bleiben erhalten.

Cloudflare Tunnel und die vertrauenswürdige Auswertung von Client-IP-Headern
bleiben nutzbar. Der eigenständige Einkaufsdienst behält seine optionalen
Cloudflare-Service-Zugangsdaten.

Die bereits vorbereitete Mengenprüfung ist enthalten: Ungültige oder unendliche
Einkaufsmengen erhalten eine verständliche Antwort mit HTTP 422. Schema 266
schützt auch direkte Datenbankschreibvorgänge; vorhandene ungültige Mengen werden
geleert, während Artikel, Herkunft und Haushaltszuordnung erhalten bleiben.
Die Übernahme der Zutaten eines Rezepts ist atomar: Eine ungültige Menge
hinterlässt weder einen teilweise gefüllten Warenkorb noch Katalogänderungen.
