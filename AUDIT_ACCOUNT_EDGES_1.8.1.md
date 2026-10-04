# Weitere Konten- und Haushaltsprüfungen für 1.8.1

Historischer Prüfstand. Die Nacharbeit ist auf 1.8.2 bereitgestellt; siehe
[Korrekturen und neue Gegenprüfungen](AUDIT_FOLLOWUP_1.8.2.md). F1/F2 wurden
behoben, F3 gegen Daten ohne Besitzer abgesichert und F4 durch Importkontingente
ergänzt. Die Kontoselbstlöschung bleibt offen.

Geprüft wurden der integrierte Worktree und die Haushalts-, Einladungs-,
Freigabe-, Sitzungs- und Importwege. Produktion läuft weiterhin auf 1.8.1.
Alle 127 installierten Release-Dateien stimmen mit dem Paketmanifest überein;
auch der lokal geprüfte Anwendungscode entspricht diesem Release. Anmeldung
ist aktiv, Gesundheits- und Bereitschaftsprüfung bestehen. Webdienst und beide
Timer laufen; Review CT117 ist ausgeschaltet und nicht eingehängt.

Anwendungscode und Produktionsdaten wurden bei diesen Checks nicht verändert.
Reproduktionen verwenden isolierte Datenbanken, synthetische Konten und eine
Fake-Importqueue. Downloads und KI-Verarbeitung wurden dabei nicht ausgelöst.

## Neue Befunde

### F1 — IMPORTANT: wiedervergebener Benutzername übernimmt fremde Freigabelinks

Freigabelinks speichern ihren Ersteller als Benutzernamen. Die Verwaltung
ordnet diesen Namen den heutigen Haushaltsmitgliedern zu. Ein Benutzername
ist nach der Löschung wieder registrierbar und damit kein stabiler Eigentümer.

Nachgewiesener Ablauf:

1. Eine Person erzeugt eine Freigabe eines globalen Rezepts und nimmt eine
   zweite Person in ihren Haushalt auf.
2. Ein Administrator löscht die Anmeldung der ersten Person. Die zweite
   Person behält den Haushalt, sieht die Freigabe aber nicht mehr.
3. Eine neue, normale Registrierung verwendet den frei gewordenen Namen.
   Sie erhält einen anderen Haushalt, kann den alten Link jedoch auflisten
   und mit **HTTP 200 widerrufen**.

Der Befund betrifft die Verwaltung von Freigaben globaler Rezepte. In dieser
Reproduktion wurden keine privaten Rezeptinhalte eines fremden Haushalts gelesen.

Quellen: `app/routes/sharing.py:381`, `app/tenant_db.py:31`,
`app/tenant_db.py:50`, `app/routes/api_auth.py:94`.

Korrektur: Freigaben an eine stabile Haushalts-ID binden und diese im selben
SQL-Schritt für Auflistung und Widerruf prüfen. Historische Zuordnungen müssen
bei der Migration geprüft werden; Namen dürfen nicht automatisch eine neue
Registrierung zum Eigentümer alter Freigaben machen.

### F2 — IMPORTANT: Importaufruf überlappt Haushaltsbeitritt und geht verloren

Der Beitritt prüft bereits gespeicherte Hintergrundaufträge. Ein HTTP-Import,
der seinen alten Haushalt schon ermittelt, aber noch keinen Auftrag gespeichert
hat, wird von dieser Prüfung nicht erfasst.

Im deterministisch synchronisierten Test wurden zwei HTTP-Aufrufe überlappt:
Ein privater Linkimport wartet nach Ermittlung des Haushalts, während dieselbe
Person eine Einladung annimmt. Der Beitritt löscht den alten Haushalt und
verschiebt dessen vorhandene Daten. Anschließend schreibt der erste Aufruf
seinen neuen Pending-Eintrag und Auftrag weiterhin mit der alten Haushalts-ID.

**Beitritt und Import antworten beide mit HTTP 200.** Der angenommene Import ist
im neuen Haushalt nicht sichtbar. Der Worker lehnt seine Verarbeitung ab:
„Import-Haushalt ist nicht mehr aktiv“. Die Pending-Zeile verweist auf einen
nicht mehr vorhandenen Haushalt. Der Test belegt die mögliche Reihenfolge;
ihre Häufigkeit unter Produktionslast wurde nicht gemessen.

Quellen: `app/tenancy.py:138`, `app/accounts.py:143`,
`app/routes/api_pending.py:292`, `app/routes/api_pending.py:320`,
`app/routes/api_pending.py:342`, `app/routes/api_share.py:139`.

Korrektur: Die Annahme eines Imports und den Haushaltswechsel gegeneinander
absichern. Vor dem Schreiben müssen Mitgliedschaft und Haushalts-ID innerhalb
der Schreibtransaktion erneut gültig sein; eine veraltete Annahme darf keinen
erfolgreichen, danach unsichtbaren Import erzeugen.

## Bereits bekannte Lücken erneut nachgewiesen

### F3 — IMPORTANT: letztes Haushaltsmitglied gelöscht, private Daten bleiben zurück

Die Admin-Löschung der letzten Anmeldung entfernt deren Haushalt durch die
bestehenden Fremdschlüssel. Ein privates Rezept bleibt dagegen mit der alten
`owner_account_id` erhalten. Der Test bestätigt: Der Haushalt fehlt, das Rezept
existiert weiterhin und alle übrigen geprüften Konten erhalten beim Abruf 404.
`PRAGMA foreign_key_check` meldet trotzdem keinen Fehler, weil diese Besitzspalte
keinen Fremdschlüssel hat.

Quellen: `app/db.py:4339`, `app/db.py:4364`, `app/tenancy.py:59`.

Die bestehende Übergabe an ein verbleibendes Haushaltsmitglied funktioniert;
dieser Fall betrifft die Löschung der letzten Person. Kontolöschung und
Datenbereinigung bleiben offene Nacharbeit.

### F4 — IMPORTANT: kein nachgewiesenes Importkontingent

Ein normales Konto konnte im Test **32 verschiedene private Linkimporte**
hintereinander mit HTTP 200 annehmen lassen. Es entstanden 32 Pending-Zeilen
und 32 Aufrufe der Fake-Queue, ohne HTTP 429. Die vorhandene Begrenzung der
Registrierung greift an diesem Endpunkt nicht. Der Test löste keine echten
Downloads oder KI-Aufrufe aus und misst keine tatsächlichen Kosten.

Quellen: `app/routes/api_pending.py:266`, `app/routes/api_pending.py:339`,
`app/jobs/task_queue.py:20`, `app/routes/api_auth.py:26`.

Die fehlenden Importkontingente bleiben bestätigt. Die Reproduktion legt kein
bestimmtes zulässiges Kontingent fest.

## Gegenprüfungen und Umfang

- Die **105 bestehenden Tests** für Konten, Gastzugang, Rollen und Haushalte
  bestehen in 41,94 s.
- **Zehn zusätzliche Auditproben** bestehen in 7,12 s. Vier davon bestätigen
  gezielt die oben beschriebenen Fehlverhalten; ihr Bestehen bedeutet keine
  Sicherheitsfreigabe. Die übrigen sechs prüfen ungültige Identitäten.
- Ohne Sitzung, mit fehlerhaftem Token, falscher Benutzer-ID sowie gesperrtem,
  gelöschtem oder abgelaufenem Konto antworten die geprüften Rezept-, Admin-
  und Import-APIs mit 401. Der geschützte Druck-PDF-Pfad leitet mit 303 zur
  Anmeldung um. Ein anonymer Adminzugriff wurde in diesen Fällen nicht reproduziert.
- Die Gegenprüfungen gelten für die aktive Kontenanmeldung. Legacy-Betrieb mit
  abgeschalteter Anmeldung und alle möglichen Endpunkte wurden hier nicht
  vollständig erneut geprüft. GUI, Xcode und native Geräte waren nicht Teil
  dieses Durchlaufs.

Die lokalen Reproduktionsproben und Beobachtungen liegen unter
`.tmp/security-checks-20261004/`. Dieses Verzeichnis ist von Git ausgeschlossen.
Aufruf mit dem vorhandenen Python-Runner:

```powershell
& 'C:\Users\Entwickler\Documents\Entwicklungen\Rezepte\.venv\Scripts\python.exe' tools/run_isolated_tests.py .tmp/security-checks-20261004/test_account_edges.py -q
```

F1 und F2 sollten vor einer weiteren Erweiterung des Mandantenbetriebs
korrigiert werden. Die bisherigen Schutzprüfungen bleiben grün, decken diese
beiden Lebenszyklusfälle aber nicht ab.
