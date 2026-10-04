# 1.8.5 — Mail-TLS und Importgrenzen

- Mailabrufe prüfen Zertifikatskette und Hostnamen vor der Anmeldung. Der
  Verbindungstest meldet Fehler und liest das Postfach ohne Flag-/Löschänderungen.
- KI- und manuell gelieferte Ordnerteile werden gemeinsam bereinigt; Punktpfade,
  Windows-Gerätenamen und überlange UTF-8-Namen können keine Fremdordner erzeugen.
- HTML-Rezeptseiten können über Canonical-Tags nur Identitäten ihres tatsächlich
  abgerufenen HTTPS-Origin beanspruchen. TikTok-Kurzlinkauflösung bleibt erhalten.
- Alle Request-Bodies sind vor Form-/JSON-Parsing begrenzt: regulär 1 MiB;
  Dateiimporte 25 MiB plus 1 MiB Overhead, Foto-/Coverimporte 10 MiB plus Overhead.
- PDF-Vorschaubilder werden mit höchstens 1600 Pixeln an der längeren Seite
  gerendert. Große Seiten erzeugen keinen entsprechend riesigen Rasterpuffer.
  Installation und Update stellen das dafür benötigte `poppler-utils` bereit.
  Der temporäre JPEG-Name entspricht dem echten Poppler-Ausgabepfad, sodass
  erzeugte Vorschauen atomar übernommen werden und keine Entwürfe zurückbleiben.
- Videoanalysen erhalten eine Abbruchschwelle von 100 MiB. Bekannte Größen
  werden vorab abgelehnt; unbekannte Streams und Fehlerlogs werden überwacht,
  abgebrochene Prozesse beendet und eigene temporäre Ordner bereinigt.
- Der Wartungs-Backup-Button ist wieder funktionsfähig. Doppelte Einkaufs-
  übermittlungen werden verhindert; ein Haushaltsbeitritt verlangt die Bestätigung
  der Datenübernahme. Fehler geben die Buttons wieder frei.
- Die Startprüfung des älteren Expo-Clients wartet bei fehlendem Server maximal
  fünf Sekunden auf die Sitzungsantwort. Abgelaufene Sitzungen löschen auch
  private Bildcaches. Kein neuer nativer App-Build enthalten.
- Das README-Konfigurationsbeispiel beschreibt den vorhandenen OpenAI-Pfad.
- Schema bleibt 263; bestehende Mailkonfiguration und Zeitpläne bleiben erhalten.
  Die Mail-Absenderfreigabe benötigt noch die erlaubten Absenderadressen.
