import SwiftUI

/// Bundled legal notice: readable before login and without a server connection.
struct ImpressumView: View {
    @Environment(\.recipeTheme) private var theme

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                Text("Rezeptregal · Zimlab")
                    .font(.headline)
                VStack(alignment: .leading, spacing: 8) {
                    Text("Anbieter").font(.title2.bold())
                    Text("Oliver Zimmermann\nc/o COCENTER\nKoppoldstr. 1\n86551 Aichach\nDeutschland")
                        .textSelection(.enabled)
                }
                VStack(alignment: .leading, spacing: 8) {
                    Text("Kontakt").font(.title2.bold())
                    Link("impressum@zimlab.org", destination: URL(string: "mailto:impressum@zimlab.org")!)
                        .frame(minHeight: 44)
                    Text("Dieses Impressum ist auch ohne Internetverbindung verfügbar.")
                        .font(.footnote)
                        .foregroundStyle(theme.muted)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(24)
        }
        .foregroundStyle(theme.ink)
        .background(theme.background)
        .navigationTitle("Impressum")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("impressum.content")
    }
}
