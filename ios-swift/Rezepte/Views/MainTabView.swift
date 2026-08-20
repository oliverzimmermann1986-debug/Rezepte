import SwiftUI

struct MainTabView: View {
    var body: some View {
        TabView {
            RecipesView()
                .tabItem { Label("Rezepte", systemImage: "fork.knife") }
            MealPlanView()
                .tabItem { Label("Wochenplan", systemImage: "calendar") }
            CartView()
                .tabItem { Label("Einkauf", systemImage: "cart") }
            AdminView()
                .tabItem { Label("Verwalten", systemImage: "gearshape") }
        }
    }
}

