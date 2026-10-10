import SwiftUI

/// User-approved app provider information remains available before login and offline.
struct LegalInformationView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.recipeTheme) private var theme

    var body: some View {
        NavigationStack {
            List {
                Section("Anbieter dieser App") {
                    Text("Oliver Zimmermann\nc/o COCENTER\nKoppoldstr. 1\n86551 Aichach\nDeutschland")
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)
                        .accessibilityIdentifier("legal.imprint.provider")
                }
                Section("Kontakt") {
                    Link(destination: URL(string: "mailto:impressum@zimlab.org")!) {
                        Label("impressum@zimlab.org", systemImage: "envelope")
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(minHeight: 44)
                    }
                    .accessibilityIdentifier("legal.imprint.contact")
                }
                Section("Webseiten der App") {
                    Link("Datenschutz", destination: AppLegalLinks.privacy)
                        .frame(minHeight: 44)
                        .accessibilityIdentifier("legal.imprint.privacy")
                    Link("Hilfe & Kontakt", destination: AppLegalLinks.support)
                        .frame(minHeight: 44)
                        .accessibilityIdentifier("legal.imprint.support")
                    Link("Impressum im Browser", destination: AppLegalLinks.imprint)
                        .frame(minHeight: 44)
                }
            }
            .scrollContentBackground(.hidden)
            .background(theme.background)
            .tint(theme.accent)
            .navigationTitle("Impressum")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Schließen") { dismiss() }
                        .accessibilityIdentifier("legal.imprint.close")
                }
            }
        }
    }
}
