import AppIntents
import SwiftUI

struct KitchenShortcutsView: View {
    var body: some View {
        List {
            Section("Einkauf per Siri") {
                Label("Einkauf hinzufügen in Quellenküche", systemImage: "basket")
                Text("Siri fragt nach dem Artikel. Sage zum Beispiel „1 Liter Milch“. Der Einkauf wird im angemeldeten Haushalt gespeichert. Bei fehlender Verbindung wartet er auf die Synchronisierung.")
            }
            Section("Heute kochen") {
                Label("Was koche ich heute mit Quellenküche?", systemImage: "fork.knife")
                Text("Ein geplanter Eintrag öffnet direkt das Rezept. Bei mehreren Gerichten öffnet sich die Tagesübersicht. Der aktuelle Wochenplan benötigt eine Serververbindung.")
            }
            Section {
                ShortcutsLink()
                Text("Beide Aktionen findest du auch in Apples Kurzbefehle-App. Für den Zugriff muss dein Gerät entsperrt und dein Konto in Quellenküche angemeldet sein.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Siri & Kurzbefehle")
        .navigationBarTitleDisplayMode(.inline)
    }
}
