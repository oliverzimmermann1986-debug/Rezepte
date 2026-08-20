import SwiftUI

struct RootView: View {
    @EnvironmentObject private var session: SessionStore

    var body: some View {
        switch session.state {
        case .checking:
            ZStack {
                AppTheme.cream.ignoresSafeArea()
                ProgressView("Sitzung wird geprüft …")
            }
        case .signedOut:
            LoginView()
        case .signedIn:
            MainTabView()
        }
    }
}

