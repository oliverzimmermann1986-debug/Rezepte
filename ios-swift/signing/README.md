# TestFlight-Signierung ohne Mac

Private Schlüssel, Zertifikate und Provisioning Profiles gehören niemals in
Git. Das Setup-Skript liest sie lokal ein und überträgt sie als verschlüsselte
GitHub-Secrets.

Benötigt werden:

1. ein expliziter App Identifier für `de.mausbaeren.rezepte`;
2. ein **Apple Distribution**-Zertifikat samt privatem Schlüssel;
3. ein **App Store Connect** Provisioning Profile für denselben Identifier;
4. ein App-Store-Connect-API-Schlüssel (`.p8`) mit Zugriff auf die App;
5. Team ID, Key ID und Issuer ID.

## Bereits vorbereitet

Der Zertifikatsantrag wurde auf diesem Windows-PC angelegt:

`C:\Users\Entwickler\Documents\AppleSigning\Rezepte\RezepteDistribution.certSigningRequest`

Der dazugehörige private Schlüssel liegt im selben Ordner als
`RezepteDistribution.key`. Diese Datei nicht hochladen, verschicken oder ins
Repository kopieren.

## Einmalige Schritte im Apple-Portal

1. Unter **Certificates, Identifiers & Profiles > Certificates** ein
   **Apple Distribution**-Zertifikat erstellen und dabei den vorbereiteten
   `.certSigningRequest` hochladen. Das erzeugte `.cer` herunterladen.
2. Falls noch nicht vorhanden, den expliziten App Identifier
   `de.mausbaeren.rezepte` registrieren.
3. Unter **Profiles** ein Profil vom Typ **App Store Connect** für genau diesen
   Identifier und das neue Zertifikat erstellen; `.mobileprovision`
   herunterladen.
4. In App Store Connect die App **Rezepte** mit diesem Bundle Identifier
   anlegen.
5. Unter **Users and Access > Integrations > App Store Connect API** einen
   Schlüssel mit Zugriff auf die App erstellen. Die `.p8`-Datei kann nur
   einmal heruntergeladen werden; zusätzlich Key ID und Issuer ID notieren.

Die drei heruntergeladenen Dateien können im lokalen Ordner
`C:\Users\Entwickler\Documents\AppleSigning\Rezepte` abgelegt werden.

Nach dem Herunterladen dieser Dateien wird auf Windows
`Complete-TestFlightSetup.ps1` ausgeführt. Danach kann auf GitHub unter
**Actions > Native iOS > Run workflow** die Option zum TestFlight-Upload
aktiviert werden.
