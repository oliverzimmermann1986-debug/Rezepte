# Öffentliche Betreiber- und Supportangaben

`GET /impressum`, `GET /support` und `GET /privacy` sind ohne Anmeldung lesbar. Web-Login, Registrierung, Hauptseite und Datenschutzerklärung verlinken diese Seiten. Die geschützten API- und Kontorouten behalten ihre bisherige Authentifizierung. Es gibt keine öffentliche Schreibschnittstelle für Betreiberangaben.

## Lokale Konfiguration

Die Umgebungsvariable **`SCRAPPER_LEGAL_CONFIG_FILE`** bezeichnet den absoluten Pfad zu einer lokalen UTF-8-JSON-Datei, beispielsweise `/etc/rezepte/operator.json`. Die tatsächlichen Angaben gehören nicht ins Git-Repository. Der Webdienst muss diese Datei lesen können; sie sollte nur durch die zuständige Administration beschreibbar sein. Der Dateipfad und Lese-/Parserfehler werden nicht öffentlich ausgegeben.

| Feld | Typ | Bedeutung |
|---|---|---|
| `name` | String, erforderlich | Tatsächlicher vollständiger Betreibername bzw. vollständige Unternehmensbezeichnung, maximal 300 Zeichen. |
| `address` | String, erforderlich | Tatsächliche ladungsfähige Anschrift dieser Betreiberinstanz, mit Zeilenumbrüchen bei Bedarf, maximal 2.000 Zeichen. |
| `email` | String, erforderlich | Tatsächlich erreichbare Kontakt-E-Mail-Adresse, maximal 254 Zeichen. ASCII-Schreibweise; internationale Domains gegebenenfalls als Punycode. |
| `additional_information` | String, optional | Weitere zutreffende Betreiberangaben als Klartext, etwa Vertretung oder Registerangaben. Maximal 8.000 Zeichen; Zeilenumbrüche erlaubt. |

Leeres Schema zur Befüllung, **kein verwendbares Impressum**:

```json
{
  "name": "",
  "address": "",
  "email": "",
  "additional_information": ""
}
```

Alle Werte werden als Text ausgegeben; HTML wird maskiert. Die Mailadresse wird zusätzlich für den `mailto:`-Link URL-kodiert. Unbekannte oder doppelte JSON-Schlüssel, falsche Typen, ungültige Mailadressen, Steuerzeichen oder eine Datei über 32 KiB machen die Konfiguration ungültig. Es gibt keine Datenübernahme aus Git-Autor, Benutzerkonto, Serverhostname oder anderen Konfigurationsdateien. Externe Impressums-Weiterleitungen sind in dieser Umsetzung nicht vorgesehen; Requestparameter ändern weder Datenquelle noch Ziel.

Beispiel für eine **lokale** systemd-Ergänzung, nachdem die Betreiberdatei mit den echten Angaben erstellt wurde:

```ini
[Service]
Environment=SCRAPPER_LEGAL_CONFIG_FILE=/etc/rezepte/operator.json
```

Die neue Umgebungsvariable benötigt beim ersten Einrichten einen regulären Dienstneustart. Änderungen am Inhalt der Datei werden ohne weiteren Neustart gelesen; Dateiänderungen möglichst atomar über eine neue Datei und Umbenennung durchführen. Die HTTP-Antworten verwenden `Cache-Control: no-store`.

## Verhalten ohne vollständige Angaben

Fehlt die Variable oder ist die Datei nicht lesbar, unvollständig oder ungültig, antworten `/impressum` und `/support` mit **HTTP 503** und einer verständlichen Hinweisseite. Es werden keine erfundenen Namen, Adressen, Mailadressen oder teilweise validierten Angaben veröffentlicht. `/privacy` bleibt mit HTTP 200 lesbar und benennt ausdrücklich die noch fehlenden Betreiberangaben; die allgemeinen Datenverarbeitungshinweise allein sind kein vollständiger Betreibernachweis.

Mit vollständiger technisch gültiger Konfiguration antworten Impressum und Support mit HTTP 200. Die Datenschutzerklärung nennt dann denselben Betreiber und Kontakt. Datenschutz- und Supportseite erläutern den vorhandenen Löschweg unter **Einstellungen → Mein Konto → Konto löschen** bzw. **Mein Konto → Konto löschen** im Browser und die Grenzen gemeinsamer Haushaltsdaten.

Die technische Validierung prüft weder tatsächliche Zustellbarkeit noch Identität, ladungsfähige Anschrift oder juristische Vollständigkeit. Ergänzende Betreiberangaben, Rechtsgrundlagen, Vereinbarungen, Datenregionen und zutreffende Fristen müssen anhand des tatsächlichen Betriebs festgelegt werden; diese Software erfindet dafür keine Werte.

## Verifikation vor Veröffentlichung

1. Echte Betreiberangaben außerhalb des Repositorys hinterlegen und Dienstumgebung setzen.
2. Ohne Konto `/impressum` und `/support` auf HTTP 200 sowie vollständige richtige Angaben prüfen; `/privacy` muss denselben Betreiber nennen.
3. Links vor Anmeldung und im angemeldeten Webbereich prüfen. Geschützte APIs müssen weiterhin ohne Anmeldung HTTP 401 liefern.
4. Erst nach diesen Prüfungen gegebenenfalls die Store-Support-URL auf die neue `/support`-Seite ändern. Eine bisherige funktionierende Support-URL darf während fehlender Betreiberkonfiguration weiterverwendet werden.

Lokale Regressionen laufen isoliert über `python tools/run_isolated_tests.py -q tests/test_legal_pages.py`. Die Tests enthalten ausschließlich synthetische Angaben und leeren die Betreiber-Umgebungsvariable vor jedem Test.
