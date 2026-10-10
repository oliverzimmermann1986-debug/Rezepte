import SwiftUI

/// Native public links; the system browser receives no API request or bearer.
struct LegalLinksView: View {
    let server: String
    let accessibilityPrefix: String
    @Environment(\.recipeTheme) private var theme

    var body: some View {
        Group {
            legalLink("Impressum", symbol: "info.circle", destination: AppLegalLinks.imprint, identifier: "imprint")
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
