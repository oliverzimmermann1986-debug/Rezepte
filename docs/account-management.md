# Konten, Sitzungen und Apple-/Google-Anmeldung

## Bedienung

Unter **Mein Konto** stehen Passwortänderung, aktive Sitzungen, einzelne Geräte
abmelden, alle Geräte abmelden und Kontolöschung zur Verfügung. Administratoren
können im Bereich **Benutzer** Konten erstellen, Rollen ändern, Konten sperren,
Passwörter neu setzen, Sitzungen widerrufen und Konten löschen. Der letzte aktive
Administrator bleibt geschützt. Haushaltsdaten werden bei einer Kontolöschung
nicht stillschweigend entfernt; bestehende Schutzmeldungen nennen einen
erforderlichen Haushaltswechsel oder noch laufende Arbeiten.

Nach dem Upgrade auf Schema 268 ist einmaliges erneutes Anmelden erforderlich.
Bestehende Konten, Passwörter und Haushalte bleiben erhalten. Neue Passwörter
brauchen mindestens zehn Zeichen und dürfen höchstens 72 UTF-8-Bytes lang sein.
Bestehende kürzere Passwörter funktionieren weiterhin.

**Abmelden** beendet die aktuelle Rezeptesitzung. **Alle Geräte abmelden** beendet
alle Rezeptesitzungen dieses Kontos. Die Anmeldung bei Apple oder Google in
anderen Apps wird dadurch nicht beendet. **Verknüpfung entfernen** entfernt
diesen Anmeldeweg, beendet seine Rezeptesitzungen und stellt den Widerruf der
Anbieterfreigabe in die dauerhafte Warteschlange. Mindestens ein Anmeldeweg bleibt
erhalten. Bei Netzfehlern wird der Anbieterwiderruf wiederholt.

Apple/Google erzeugen ohne bestehende Verknüpfung ein neues Konto. Gleiche
E-Mail-Adressen führen niemals zu einer automatischen Zusammenführung. Ein
bestehendes Konto wird unter **Mein Konto → Anmeldewege** bewusst verbunden.
Neue Verknüpfungen brauchen das aktuelle Passwort oder eine innerhalb von fünf
Minuten bestätigte Anbieteranmeldung. Bereits verbundene Anbieter können zur
erneuten Bestätigung verwendet werden. Dafür muss dasselbe Anbieterkonto gewählt
werden. Passwortänderung, Kontolöschung und Trennung verlangen ebenfalls diese
erneute Bestätigung.

## Rollen und Rechte

Administratoren vergeben unter **Benutzer** eine von vier Rollen:

| Rolle | Rechte |
| --- | --- |
| Gast (`guest`) | Öffentliche Rezepte lesen; keine Haushaltsdaten bearbeiten. |
| Benutzer (`user`) | Rezepte und private Varianten, Favoriten, Einkauf und Wochenplanung nutzen. |
| Vollbenutzer (`full_user`) | Alle Benutzerrechte sowie private Link-, Foto- und PDF-Importe für den eigenen Haushalt. |
| Admin (`admin`) | Alle Rechte einschließlich globaler Importe, Benutzerverwaltung und Serveradministration. |

Eine Registrierung mit Apple, Google oder Benutzername/Passwort legt ein normales
Benutzerkonto an. Die Art der Anmeldung verleiht keine Administratorrechte. Wird
ein Anbieter mit einem bestehenden Konto verbunden, bleibt dessen Rolle erhalten.

Normale Konten können sichtbare Rezepte lesen, eine eigene private Variante
erstellen und Rezepte ihres Haushalts manuell bearbeiten. Die globale Vorlage
bleibt unverändert. Favoriten, Einkauf, Wochenplanung und die eigene
Kontoverwaltung bleiben verfügbar. Eigene Varianten lassen sich auch dann ohne KI
anlegen, wenn die Vorlage noch nicht vollständig extrahiert ist; die Kopie wird
nicht automatisch zur Extraktion eingeplant.

Vollbenutzer können private Links, Fotos und PDF-Dateien importieren, ihre
Importvorschläge bearbeiten, ergänzende Fotos per OCR auslesen und ihre eigenen
offenen Importe erneut analysieren. Sie können weder globale Importe noch
Importe anderer Haushalte bearbeiten. Globale Importe, KI-Übersetzung,
Bildgenerierung, KI-Nährwerte, erneute Extraktion bestehender Rezepte und die
KI-Einkaufsoptimierung bleiben Administratoren vorbehalten. Diese Grenzen werden
auf dem Server durchgesetzt. Geteilte Links starten für normale Benutzer keinen
Hintergrundimport. E-Mail-Import und dessen Zeitplanung sind vollständig entfernt.

Die Verwaltung anderer Benutzer, Serverkonfiguration und administrative Jobs
bleiben ebenfalls Administratoren vorbehalten. Ein Gast kann keine Variante
anlegen und keine Rezept- oder Haushaltsdaten bearbeiten. Ein angemeldetes Konto
mit Gastrolle kann weiterhin sein eigenes Passwort, seine Anmeldewege und
Sitzungen verwalten sowie sein Konto löschen; die erforderliche erneute
Anmeldebestätigung bleibt bestehen. Der anonyme Gastzugang hat kein solches Konto.

## Servereinrichtung

Anbieter werden nur angezeigt, wenn ihre serverseitige Konfiguration vollständig
ist. Ohne diese Einrichtung bleiben Benutzername/Passwort und Gastzugang nutzbar.
Die Einstellungen werden ausschließlich aus der Prozessumgebung gelesen; sie
gehören nicht in die exportierbare `config.yaml`, Git oder mobile App.

Gemeinsame Variable:

```text
REZEPTE_PUBLIC_URL=https://rezepte.mausbaeren.me
```

Google benötigt einen OAuth-Client für eine **Webanwendung** mit eingerichteter
Zustimmungsseite und exakt registrierter Weiterleitungsadresse:

```text
https://rezepte.mausbaeren.me/api/auth/google/callback
REZEPTE_GOOGLE_CLIENT_ID=<Client-ID>
REZEPTE_GOOGLE_CLIENT_SECRET=<Client-Secret>
```

Apple benötigt eine für Sign in with Apple eingerichtete primäre App-ID, eine
zugehörige **Services ID**, die registrierte Webdomain/Return URL sowie einen
zugehörigen Sign-in-Schlüssel. Dieser Server verwendet den gemeinsamen Webflow
auch für die native App; als Client-ID dient deshalb die Services ID.

```text
https://rezepte.mausbaeren.me/api/auth/apple/callback
REZEPTE_APPLE_CLIENT_ID=<Services-ID>
REZEPTE_APPLE_TEAM_ID=<Team-ID>
REZEPTE_APPLE_KEY_ID=<Key-ID>
REZEPTE_APPLE_PRIVATE_KEY_PATH=/opt/scrapper/data/auth/apple-sign-in.p8
```

Environment-Datei und Apple-Schlüssel nur für den Servicebenutzer lesbar ablegen
(z. B. Modus 0600), per systemd `EnvironmentFile` laden und den Dienst neu starten.
Keine Secrets als Kommandozeilenargumente, in Screenshots oder in Support-Chats
verwenden. Der vorhandene `web.secret_key` verschlüsselt gespeicherte
Widerrufsschlüssel und kurzlebige Anmeldevorgänge. Ihn zusammen mit der Datenbank
geschützt sichern. Ein Austausch macht bisher verschlüsselte Anbieterfreigaben
unlesbar; vor einer geplanten Rotation ausstehende Widerrufe abarbeiten.

`GET /api/auth/providers` zeigt ausschließlich Anbietername und Verfügbarkeit.
Ein `enabled: true` bestätigt vollständige lokale Konfiguration, nicht die
Freigabe durch Apple/Google oder einen erfolgreichen echten Anmeldevorgang.

## Betrieb und Abnahme

Der Server prüft feste Anbieterendpunkte, JWT-Signatur, Aussteller, Zielclient,
Ablauf und Nonce. Webanmeldungen sind an einen HttpOnly-Browsercookie gebunden;
native Übergaben an einen einmaligen, 60 Sekunden gültigen Code und PKCE-S256.
Der native Callback lautet `de.mausbaeren.rezepte://auth/callback`. Ein Bearertoken
steht niemals in dieser URL. Apple verwendet eine Cross-Site-Formularantwort;
deren eng begrenzte CSRF-Ausnahme prüft zusätzlich einmaligen State und die
Browserbindung. Alle übrigen Cookie-Schreibzugriffe behalten die Herkunftsprüfung.

Uvicorn entfernt die Queryparameter der Anbieter-Callbacks aus seinem Accesslog.
Vorgeschaltete Proxy-/Cloudflare-Logs sind separat so zu konfigurieren, dass sie
keine OAuth-Codes oder State-Werte speichern. Niemals Request-Bodies von Login,
Passwortänderung oder Tokenaustausch protokollieren.

Vor Aktivierung beide Anbieter auf dem echten HTTPS-Server testen: neues Konto,
bestehende Verknüpfung, Abbruch, falsches Anbieterkonto, erneute Bestätigung,
Abmeldung, Einzelwiderruf und Trennung. Die automatisierten Tests verwenden
lokal erzeugte Signaturschlüssel und simulierte Anbieterantworten. Sie ersetzen
nicht die echte Anbieterfreigabe oder die Prüfung auf einem iPhone.

Offizielle Einrichtung: [Google OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect),
[Google Webserver OAuth](https://developers.google.com/identity/protocols/oauth2/web-server),
[Sign in with Apple REST API](https://developer.apple.com/documentation/signinwithapplerestapi),
[Apple Kontolöschung und Tokenwiderruf](https://developer.apple.com/documentation/technotes/tn3194-handling-account-deletions-and-revoking-tokens-for-sign-in-with-apple).
