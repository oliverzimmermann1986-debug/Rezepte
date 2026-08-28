# App-Review-Notizen – Quellenküche 1.1

## Eigenständiges Produktkonzept

Quellenküche ist kein allgemeiner Rezept-Reader und keine umbenannte
Template-App. Der zentrale Ablauf lautet:

1. Eine Rezeptquelle aus Website, Pinterest, YouTube, TikTok, Instagram, Foto
   oder PDF in den Eingang legen.
2. Zutaten und Schritte serverseitig erkennen, die Originalquelle aber sichtbar
   am Rezept bewahren.
3. Unsichere Ergebnisse in einer manuellen Prüfwarteschlange bearbeiten.
4. Aus Rezeptzutaten eine lokale, nach Supermarktbereichen sortierte
   Einkaufsliste mit Autovervollständigung aufbauen.
5. Rezeptbilder generieren, nachdem vorhandene Bilder checksummiert gesichert
   wurden; Original und Neufassung können verglichen und wiederhergestellt
   werden.

## Technische Eigenständigkeit

- Der ausgelieferte Client unter `ios-swift/` ist nativ in Swift und SwiftUI.
- Es gibt keine WebView und keine gekaufte oder fremde UI-Vorlage.
- Es sind keine UI-Pakete oder Template-Abhängigkeiten eingebunden.
- Farbwelten, Navigation, Share Extension, Einkaufskatalog und Bildverlauf sind
  projektspezifischer Quellcode in diesem Repository.
- Der Gastzugang kann das Rezeptarchiv ohne neues Konto rein lesend prüfen;
  Schreibaktionen werden zusätzlich serverseitig abgewiesen.
- `native-ios/` bleibt ausschließlich als alter Expo-Vergleichsstand im
  Repository und gehört nicht zum neuen SwiftUI-Binary.

## Vorschlag für das Feld „App Review Notes“

> Quellenküche 1.1 is a native SwiftUI redesign centered on source-aware recipe
> capture. Reviewers can share or paste a recipe URL from a website, Pinterest,
> YouTube, TikTok or Instagram, or upload a photo/PDF. The original source stays
> visible while uncertain extraction results enter a manual review queue. The
> shopping list uses an account-local ingredient catalog with supermarket
> categories and icons. Administrators can generate recipe images only after
> existing images are checksum-backed up, compare both versions and restore an
> original. Reviewers may also choose "Als Gast ansehen" for a read-only archive
> tour without creating an account. No purchased app template, WebView or
> third-party UI kit is used.

Vor der Einreichung in App Store Connect zusätzlich ein funktionierendes
Review-Konto und kurze Schritte für einen Beispielimport angeben.
