import SwiftUI

struct MainTabView: View {
    @EnvironmentObject private var session: SessionStore
    @StateObject private var shopping = ShoppingSyncStore()

    var body: some View {
        TabView {
            TodayView()
                .tabItem { Label("Heute", systemImage: "sun.max.fill") }
            RecipesView()
                .tabItem { Label("Archiv", systemImage: "square.stack.3d.up.fill") }
            if !session.readOnly {
                MealPlanView()
                    .tabItem { Label("Plan", systemImage: "calendar") }
                CartView()
                    .tabItem { Label("Einkauf", systemImage: "basket.fill") }
            } else {
                GuestHouseholdPreview(title: "Wochenplan", symbol: "calendar", message: "Mit einem Konto planst du die Woche für deinen Haushalt. Private Planungen sind für Gäste nicht sichtbar.")
                    .tabItem { Label("Plan", systemImage: "calendar") }
                GuestHouseholdPreview(title: "Einkauf", symbol: "basket", message: "Mit einem Konto sammelst du Zutaten für deinen Haushalt. Private Einkaufslisten sind für Gäste nicht sichtbar.")
                    .tabItem { Label("Einkauf", systemImage: "basket.fill") }
            }
            SettingsView()
                .tabItem { Label("Einstellungen", systemImage: "slider.horizontal.3") }
        }
        .environmentObject(shopping)
        .task(id: session.identity) {
            await KitchenTimerStore.shared.activate(session: session)
        }
        .task(id: session.identity) { await shopping.activate(session: session) }
        .onReceive(NotificationCenter.default.publisher(for: Notification.Name("KitchenHouseholdChanged"))) { _ in
            do { try shopping.invalidateHousehold() }
            catch { session.alertMessage = error.localizedDescription }
        }
    }
}

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
