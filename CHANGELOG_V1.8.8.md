# 1.8.8 – Anmeldung, Last und sichere Upgrades

- HTML-Formulare senden mit `strict-origin` wieder einen überprüfbaren Origin.
  Header und Registrierungs-Meta sind abgestimmt; Pfade und Einladungstokens
  werden nicht als Referer weitergereicht. Fremde und `null` Origins bleiben gesperrt,
  einschließlich Logout ohne Sitzungscookie.
- HTML und native Anmeldung teilen einen normalisierten Zähler je IP und
  Benutzername. Fünf Fehlversuche sperren diesen Zugang; ein zusätzlicher
  IP-Zähler begrenzt breit gestreute Versuche auf 100. Ein anderes Konto hinter
  derselben IP bleibt unter dieser Gesamtgrenze nutzbar. Direkte Proxyheader
  werden ignoriert; vertrauenswürdige Proxyketten werden von rechts geprüft.
- Vor Beginn einer HTTP-Antwort führt SQLite-Schreibkonkurrenz zu 503 mit
  `Retry-After: 1`, statt interne Datenbankfehler an Clients weiterzugeben.
- Schema 265 trennt aufeinanderfolgende Worker-Abstürze von regulären Retries.
  Auch die Wiederherstellung des Bild-Backfills verwendet den eigenen Zähler.
  Fehlende Quell-URLs gelöschter Rezepte werden aus erhaltenen Original-URLs
  ergänzt; interne private Schlüssel werden nicht als Quellbeleg verwendet.
- Die Rezeptdetail-Datenabfragen teilen eine kurzlebige, schreibgeschützte
  Verbindung. Haushaltsrechte werden weiterhin pro Anfrage geprüft.
- Neue Web-/CLI-Prozesse beachten `paths.db_path`. Job-Locks liegen neben der
  konfigurierten Datenbank. Echte Browser-Tests lehnen jeden Datenbankpfad
  außerhalb ihrer Sandbox vor dem ersten Zugriff ab.
- Zusätzliche Regressionen prüfen echte Chromium-Formulare, zwei Browserkonten
  mit Einladung, 50 parallele HTTP-Lesezugriffe, Schreibkonkurrenz sowie den
  Upgradepfad von Schema 260 und den Rollback aus dem Sicherheitsbackup.

Installierte Prüfungen verwenden temporäre Daten, synthetische Konten und keine
externen KI-, Mail- oder Download-Aufrufe. Ein lokaler Test vor der Pfadkorrektur
hat versehentlich die vorhandene Windows-Datenbank unter `C:\opt\scrapper`
verwendet; der Nebeneffekt ist im Audit dokumentiert. Keine Altdateien gelöscht.
