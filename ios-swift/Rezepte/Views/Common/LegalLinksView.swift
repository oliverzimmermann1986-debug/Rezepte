import SwiftUI

/// Offline app imprint and public links; no API credentials are forwarded.
struct LegalLinksView: View {
    let server: String
    let accessibilityPrefix: String
    @Environment(\.recipeTheme) private var theme
    @State private var showsImprint = false

    var body: some View {
        Group {
            Button { showsImprint = true } label: {
                Label("Impressum", systemImage: "info.circle")
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .foregroundStyle(theme.accent)
            .accessibilityHint("Zeigt die Anbieterangaben dieser App auch ohne Internetverbindung.")
            .accessibilityIdentifier("\(accessibilityPrefix).imprint")
            .sheet(isPresented: $showsImprint) { LegalInformationView() }
            legalLink("Datenschutz", symbol: "hand.raised", destination: AppLegalLinks.privacy, identifier: "privacy")
            legalLink("Hilfe & Kontakt", symbol: "questionmark.circle", destination: AppLegalLinks.support, identifier: "support")

            if let url = AppLegalLinks.additionalServerPrivacyURL(for: server) {
                Link(destination: url) {
                    VStack(alignment: .leading, spacing: 4) {
                        Label("Datenschutz des Servers", systemImage: "network")
                        if let host = url.host {
                            Text(url.port.map { "\(host):\($0)" } ?? host)
                                .font(.caption)
                                .foregroundStyle(theme.muted)
                        }
                    }
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .contentShape(Rectangle())
                }
                .accessibilityHint("Öffnet die Datenschutzhinweise deines ausgewählten Servers im Browser.")
                .accessibilityIdentifier("\(accessibilityPrefix).server-privacy")
            }
        }
        .font(.body)
        .tint(theme.accent)
    }

    private func legalLink(_ title: String, symbol: String, destination: URL, identifier: String) -> some View {
        Link(destination: destination) {
            Label(title, systemImage: symbol)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                .contentShape(Rectangle())
        }
        .accessibilityHint("Öffnet die öffentliche Seite von Rezeptregal im Browser.")
        .accessibilityIdentifier("\(accessibilityPrefix).\(identifier)")
    }
}
