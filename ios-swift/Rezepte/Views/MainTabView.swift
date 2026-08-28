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
            }
            SettingsView()
                .tabItem { Label("Einstellungen", systemImage: "slider.horizontal.3") }
        }
    }
}
