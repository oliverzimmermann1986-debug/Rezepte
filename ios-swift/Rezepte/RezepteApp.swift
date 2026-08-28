import SwiftUI

@main
struct RezepteApp: App {
    @StateObject private var session = SessionStore()
    @StateObject private var themeStore = ThemeStore()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(session)
                .environmentObject(themeStore)
                .environment(\.recipeTheme, themeStore.theme)
                .tint(themeStore.theme.accent)
                .preferredColorScheme(themeStore.appearance.colorScheme)
                .task { await session.restore() }
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
                    await session.drainSharedImports()
                }
            }
        }
    }
}
