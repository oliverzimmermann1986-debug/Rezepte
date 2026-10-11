import AppIntents

struct AddKitchenShoppingIntent: AppIntent {
    static var title: LocalizedStringResource = "Artikel zur Einkaufsliste hinzufügen"
    static var description = IntentDescription("Fügt einen Artikel zum Einkauf deines Haushalts hinzu. Mengen wie 1 Liter Milch werden erkannt.")
    static var openAppWhenRun: Bool = true
    static var authenticationPolicy: IntentAuthenticationPolicy = .requiresLocalDeviceAuthentication

    @Parameter(title: "Artikel", requestValueDialog: "Was soll auf die Einkaufsliste?")
    var item: String

    static var parameterSummary: some ParameterSummary { Summary("\(\.$item) zur Einkaufsliste hinzufügen") }

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let synchronized = try await KitchenShortcutCoordinator.shared.addShopping(item)
        if synchronized {
            return .result(dialog: "Der Artikel wurde deiner Einkaufsliste hinzugefügt.")
        }
        return .result(dialog: "Der Artikel ist auf diesem Gerät gespeichert und wird bei Verbindung synchronisiert.")
    }
}

struct OpenTodayKitchenIntent: AppIntent {
    static var title: LocalizedStringResource = "Heutiges Rezept öffnen"
    static var description = IntentDescription("Öffnet das heutige Rezept aus deinem Wochenplan. Bei mehreren Rezepten erscheint die Tagesübersicht.")
    static var openAppWhenRun: Bool = true
    static var authenticationPolicy: IntentAuthenticationPolicy = .requiresLocalDeviceAuthentication

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
        let count = try await KitchenShortcutCoordinator.shared.openToday()
        if count == 0 { return .result(dialog: "Für heute ist noch kein Rezept geplant. Dein Tagesplan ist geöffnet.") }
        if count == 1 { return .result(dialog: "Dein heutiges Rezept ist geöffnet.") }
        return .result(dialog: "Heute sind mehrere Rezepte geplant. Wähle eines in der Tagesübersicht.")
    }
}

struct KitchenAppShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: AddKitchenShoppingIntent(),
                    phrases: ["Einkauf hinzufügen in \(.applicationName)", "Füge einen Artikel in \(.applicationName) hinzu"],
                    shortTitle: "Einkauf hinzufügen", systemImageName: "basket")
        AppShortcut(intent: OpenTodayKitchenIntent(),
                    phrases: ["Was koche ich heute mit \(.applicationName)", "Öffne das heutige Rezept in \(.applicationName)"],
                    shortTitle: "Heutiges Rezept", systemImageName: "fork.knife")
    }
}
