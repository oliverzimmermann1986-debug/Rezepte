import SwiftUI

@main
struct RezepteApp: App {
    @StateObject private var session = SessionStore()
    @StateObject private var themeStore = ThemeStore()
    @StateObject private var offline = OfflineRecipeStore()
    @StateObject private var searches = SavedRecipeSearchStore()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(session)
                .environmentObject(themeStore)
                .environmentObject(offline)
                .environmentObject(searches)
                .environment(\.recipeTheme, themeStore.theme)
                .tint(themeStore.theme.accent)
                .preferredColorScheme(themeStore.appearance.colorScheme)
                .task {
                    KitchenShortcutCoordinator.shared.bind(session: session)
                    await session.restore()
                }
                .onChange(of: session.identity) { _, _ in
                    offline.deactivate()
                    searches.deactivate()
                    KitchenShortcutCoordinator.shared.clear()
                }
                .alert(
                    "Hinweis",
                    isPresented: Binding(
                        get: { session.alertMessage != nil },
                        set: { if !$0 { session.alertMessage = nil } }
                    )
                ) {
                    Button("OK", role: .cancel) {}
                } message: {
                    Text(session.alertMessage ?? "")
                }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                Task {
                    await session.refreshAccess()
                    await offline.refreshContext(session: session)
                    await searches.activate(session: session)
                    await session.drainSharedImports()
                }
            }
        }
    }
}
