# App-Review-Notizen – Quellenküche 1.2

## Eigenständiges Produktkonzept

Quellenküche ist kein allgemeiner Rezept-Reader und keine umbenannte
Template-App. Der zentrale Ablauf lautet:

1. Eine Rezeptquelle aus Website, Pinterest, YouTube, TikTok, Instagram, Foto
   oder PDF in den Eingang legen.
2. Zutaten und Schritte serverseitig erkennen, die Originalquelle aber sichtbar
   am Rezept bewahren.
3. Im Quellenwächter den gespeicherten Quellstand mit der aktuellen Seite
   vergleichen. Änderungen erscheinen als Diff und überschreiben das Rezept
   niemals automatisch.
4. Unsichere Ergebnisse im Rezept-TÜV und in einer manuellen
   Prüfwarteschlange bearbeiten.
5. Aus Rezeptzutaten und wiederkehrendem Haushaltsbedarf eine nach
   Supermarktbereichen sortierte
   Einkaufsliste mit Autovervollständigung aufbauen.
6. Mehrere geplante Gerichte im Menü-Dirigenten rückwärts zu einer gemeinsamen
   Servierzeit koordinieren. Herd, Ofen und aktive Kochkapazität werden als
   begrenzte Ressourcen behandelt.
7. Eine fehlende Zutat im Substitutionslabor als nachvollziehbare Variante
   ersetzen. Konkrete Vorher-/Nachher-Mengen und Einschränkungen bleiben
   sichtbar; das Original wird nie überschrieben.
8. Rezeptbilder generieren, nachdem vorhandene Bilder checksummiert gesichert
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

> Quellenküche 1.2 is a native SwiftUI app centered on source-aware recipe
> capture. Reviewers can share or paste a recipe URL from a website, Pinterest,
> YouTube, TikTok or Instagram, or upload a photo/PDF. The original source stays
> visible. The in-app Source Watcher stores a text fingerprint, compares a
> later source revision, and presents a structured diff without ever
> overwriting the saved recipe. A deterministic Recipe Check highlights missing
> servings, ingredients, steps and duplicate entries. The shopping list merges
> planned recipe ingredients with recurring household purchases and uses an
> account-local ingredient catalog with supermarket categories and icons.
> Its Menu Conductor schedules several dishes backwards from one serving time
> while respecting oven, burner and active-cook capacity. The Substitution Lab
> previews exact before/after ingredients and creates a traceable recipe
> variant without overwriting the original.
> Administrators can generate recipe images only after
> existing images are checksum-backed up, compare both versions and restore an
> original. Reviewers may also choose "Als Gast ansehen" for a read-only archive
> tour without creating an account. No purchased app template, WebView or
> third-party UI kit is used.

Vor der Einreichung in App Store Connect zusätzlich ein funktionierendes,
nicht ablaufendes Review-Konto und kurze Schritte für einen Beispielimport
angeben. In den ersten drei Screenshots Menü-Dirigent, Quellenwächter und
Substitutionslabor zeigen, nicht nur die Rezeptübersicht.
