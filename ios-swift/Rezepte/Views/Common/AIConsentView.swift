import SwiftUI

private struct AIConsentSheet: ViewModifier {
    @ObservedObject var coordinator: AIConsentCoordinator
    @EnvironmentObject private var session: SessionStore

    func body(content: Content) -> some View {
        content
            .sheet(item: Binding(get: { coordinator.pending }, set: { if $0 == nil { coordinator.cancel() } })) { request in
                AIConsentView(request: request, cancel: { coordinator.cancel() }, approve: {
                    coordinator.approve(identity: session.identity, server: session.savedServer)
                })
            }
            .onChange(of: session.identity) { _, _ in coordinator.cancel() }
            .onChange(of: session.savedServer) { _, _ in coordinator.cancel() }
            .onDisappear { coordinator.cancel() }
    }
}

extension View {
    func aiConsentPrompt(_ coordinator: AIConsentCoordinator) -> some View {
        modifier(AIConsentSheet(coordinator: coordinator))
    }
}

private struct AIConsentView: View {
    let request: AIConsentRequest
    let cancel: () -> Void
    let approve: () -> Void
    @Environment(\.recipeTheme) private var theme

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    Text(request.action.title).font(.title2.bold()).foregroundStyle(theme.ink)
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Empfänger: OpenAI").font(.headline)
                        Text("Dein Server \(request.host) übermittelt die folgenden Daten zur KI-Verarbeitung an OpenAI.")
                    }
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Diese Daten werden verarbeitet").font(.headline)
                        Text(request.action.dataDescription)
                    }
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Zweck").font(.headline)
                        Text(request.action.purpose)
                    }
                    Text("Bitte sende keine unnötigen personenbezogenen oder vertraulichen Daten. Deine Zustimmung gilt nur für diese Aktion. Ohne Zustimmung wird nichts übertragen.")
                        .foregroundStyle(theme.muted)
                    Link("Datenschutz", destination: AppLegalLinks.privacy)
                        .frame(minHeight: 44)
                        .accessibilityIdentifier("ai-consent.privacy")
                    if let serverPrivacy = AppLegalLinks.additionalServerPrivacyURL(for: request.server) {
                        Link("Datenschutz des Servers", destination: serverPrivacy).frame(minHeight: 44)
                    }
                    Button(action: approve) {
                        Text("Zustimmen und fortfahren")
                            .fontWeight(.semibold)
                            .frame(maxWidth: .infinity, minHeight: 48)
                    }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("ai-consent.approve")
                    Button("Abbrechen", role: .cancel, action: cancel)
                        .frame(maxWidth: .infinity, minHeight: 44)
                        .accessibilityIdentifier("ai-consent.cancel")
                }
                .padding(24)
                .fixedSize(horizontal: false, vertical: true)
            }
            .background(theme.background)
            .navigationTitle("KI-Verarbeitung")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Abbrechen", action: cancel) } }
        }
    }
}
