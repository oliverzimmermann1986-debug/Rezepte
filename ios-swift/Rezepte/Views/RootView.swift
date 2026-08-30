import SwiftUI

struct RootView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme

    var body: some View {
        switch session.state {
        case .checking:
            ZStack {
                theme.background.ignoresSafeArea()
                ProgressView("Sitzung wird geprüft …")
            }
        case .signedOut:
            LoginView()
        case .signedIn:
            MainTabView()
        }
    }
}
