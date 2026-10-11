import SwiftUI

struct MainTabView: View {
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var offline: OfflineRecipeStore
    @EnvironmentObject private var searches: SavedRecipeSearchStore
    @StateObject private var shopping = ShoppingSyncStore()
    @ObservedObject private var shortcuts = KitchenShortcutCoordinator.shared
    @State private var selection = 0
    @State private var shortcutRecipe: ShortcutRecipe?

    var body: some View {
        TabView(selection: $selection) {
            TodayView()
                .tabItem { Label("Heute", systemImage: "sun.max.fill") }
                .tag(0)
            RecipesView()
                .tabItem { Label("Archiv", systemImage: "square.stack.3d.up.fill") }
                .tag(1)
            if !session.readOnly {
                MealPlanView()
                    .tabItem { Label("Plan", systemImage: "calendar") }
                    .tag(2)
                CartView()
                    .tabItem { Label("Einkauf", systemImage: "basket.fill") }
                    .tag(3)
            } else {
                GuestHouseholdPreview(title: "Wochenplan", symbol: "calendar", message: "Mit einem Konto planst du die Woche für deinen Haushalt. Private Planungen sind für Gäste nicht sichtbar.")
                    .tabItem { Label("Plan", systemImage: "calendar") }
                    .tag(2)
                GuestHouseholdPreview(title: "Einkauf", symbol: "basket", message: "Mit einem Konto sammelst du Zutaten für deinen Haushalt. Private Einkaufslisten sind für Gäste nicht sichtbar.")
                    .tabItem { Label("Einkauf", systemImage: "basket.fill") }
                    .tag(3)
            }
            SettingsView()
                .tabItem { Label("Einstellungen", systemImage: "slider.horizontal.3") }
                .tag(4)
        }
        .environmentObject(shopping)
        .task(id: session.identity) {
            await KitchenTimerStore.shared.activate(session: session)
        }
        .task(id: session.identity) {
            shortcuts.bind(shopping: shopping, session: session)
            await shopping.activate(session: session)
        }
        .task(id: session.identity) { await offline.activate(session: session) }
        .task(id: session.identity) { await searches.activate(session: session) }
        .onChange(of: shortcuts.route?.id) { _, _ in applyShortcutRoute() }
        .onAppear { applyShortcutRoute() }
        .sheet(item: $shortcutRecipe) { route in
            NavigationStack {
                RecipeDetailView(recipeID: route.id)
                    .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Schließen") { shortcutRecipe = nil } } }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .recipesChanged)) { _ in
            guard !session.readOnly, !session.isOfflineSession else { return }
            let identity = session.identity
            Task {
                guard session.identity == identity else { return }
                try? await shopping.refresh()
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("KitchenHouseholdChanged"))) { _ in
            do { try shopping.invalidateHousehold() }
            catch { session.alertMessage = error.localizedDescription }
            do { try offline.invalidateHousehold() }
            catch { session.alertMessage = error.localizedDescription }
            searches.invalidateHousehold()
            shortcuts.clear()
        }
    }

    private func applyShortcutRoute() {
        guard let route = shortcuts.route else { return }
        defer { shortcuts.consumeRoute(id: route.id) }
        guard route.identity == session.identity, !session.readOnly else { return }
        switch route.destination {
        case .shopping: selection = 3
        case .today: selection = 0
        case .recipe(let id): selection = 0; shortcutRecipe = ShortcutRecipe(id: id)
        }
    }
}

private struct ShortcutRecipe: Identifiable { let id: Int }

private struct GuestHouseholdPreview: View {
    let title: String
    let symbol: String
    let message: String
    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme

    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                ContentUnavailableView(title, systemImage: symbol, description: Text(session.canManageOwnAccount ? "Für Haushaltsfunktionen brauchst du mindestens die Rolle Benutzer. Deine Anmeldung verwaltest du unter Einstellungen → Mein Konto." : message))
                if !session.canManageOwnAccount {
                    Button("Konto erstellen") { session.startRegistration() }
                        .buttonStyle(.borderedProminent)
                        .frame(minHeight: 44)
                    Button("Anmelden") { session.signOut() }
                        .frame(minHeight: 44)
                }
            }
            .padding()
            .background(theme.background)
            .navigationTitle(title)
        }
    }
}
