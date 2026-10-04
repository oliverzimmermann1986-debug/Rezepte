import SwiftUI

struct MainTabView: View {
    @EnvironmentObject private var session: SessionStore

    var body: some View {
        TabView {
            if !session.readOnly {
                InboxView()
                    .tabItem { Label("Eingang", systemImage: "tray.and.arrow.down.fill") }
            }
            RecipesView()
                .tabItem { Label("Archiv", systemImage: "square.stack.3d.up.fill") }
            if !session.readOnly {
                MealPlanView()
                    .tabItem { Label("Heute", systemImage: "flame.fill") }
                CartView()
                    .tabItem { Label("Einkauf", systemImage: "basket.fill") }
            } else {
                GuestHouseholdPreview(title: "Wochenplan", symbol: "calendar", message: "Mit einem Konto planst du die Woche für deinen Haushalt. Private Planungen sind für Gäste nicht sichtbar.")
                    .tabItem { Label("Heute", systemImage: "flame.fill") }
                GuestHouseholdPreview(title: "Einkauf", symbol: "basket", message: "Mit einem Konto sammelst du Zutaten für deinen Haushalt. Private Einkaufslisten sind für Gäste nicht sichtbar.")
                    .tabItem { Label("Einkauf", systemImage: "basket.fill") }
            }
            SettingsView()
                .tabItem { Label("Einstellungen", systemImage: "slider.horizontal.3") }
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
                ContentUnavailableView(title, systemImage: symbol, description: Text(message))
                Button("Konto erstellen") { session.startRegistration() }
                    .buttonStyle(.borderedProminent)
                    .frame(minHeight: 44)
                Button("Anmelden") { session.signOut() }
                    .frame(minHeight: 44)
            }
            .padding()
            .background(theme.background)
            .navigationTitle(title)
        }
    }
}
