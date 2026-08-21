import SwiftUI

@main
struct RezepteApp: App {
    @StateObject private var session = SessionStore()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(session)
                .tint(AppTheme.butter)
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
                Task { await session.drainSharedImports() }
            }
        }
    }
}
