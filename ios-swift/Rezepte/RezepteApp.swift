import SwiftUI

@main
struct RezepteApp: App {
    @StateObject private var session = SessionStore()

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
    }
}

