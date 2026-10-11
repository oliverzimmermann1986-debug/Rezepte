import SwiftUI

private struct AIConsentSheet: ViewModifier {
    @ObservedObject var coordinator: AIConsentCoordinator
    let allowsOneTime: Bool
    @EnvironmentObject private var session: SessionStore

    func body(content: Content) -> some View {
        content
            .sheet(item: Binding(get: { coordinator.pending }, set: { if $0 == nil { coordinator.cancel() } })) { request in
                AIConsentView(request: request, accountName: session.username, allowsOneTime: allowsOneTime,
                              cancel: { coordinator.cancel() }, approve: { rememberLinkImports in
                    coordinator.approve(session: session, rememberLinkImports: rememberLinkImports)
                })
            }
            .onChange(of: session.identity) { _, _ in coordinator.cancel() }
            .onChange(of: session.savedServer) { _, _ in coordinator.cancel() }
            .onDisappear { coordinator.cancel() }
    }
}

extension View {
    func aiConsentPrompt(_ coordinator: AIConsentCoordinator, allowsOneTime: Bool = true) -> some View {
        modifier(AIConsentSheet(coordinator: coordinator, allowsOneTime: allowsOneTime))
    }
}

private struct AIConsentView: View {
    let request: AIConsentRequest
    let accountName: String
    let allowsOneTime: Bool
    let cancel: () -> Void
    let approve: (Bool) -> Void
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
                    if request.canRememberLinkImports {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("Einmal zustimmen, Links automatisch importieren").font(.headline)
                            Text("Konto: \(accountName)")
                            Text("Server: \(request.server)")
                                .font(.caption).foregroundStyle(theme.muted)
                            Text("Die Freigabe gilt auf diesem Gerät für dein Konto und diesen Server. Sie umfasst Linkimporte einschließlich der beschriebenen Folgeschritte. Bereits auf diesem Gerät wartende, noch keinem Konto zugeordnete Links werden ebenfalls verarbeitet.")
                            Text("Künftig beginnt der Import geteilter Links beim Öffnen der App. Du kannst die Freigabe unter Einstellungen → Automatischer Linkimport jederzeit widerrufen. Abmelden oder ein Haushaltswechsel löscht sie. Andere KI-Aktionen, Foto- und PDF-Uploads sind nicht eingeschlossen.")
                            Text("Bereits vom Server angenommene Importe können nach dem Widerruf noch fertiggestellt werden.")
                                .font(.caption).foregroundStyle(theme.muted)
                        }
                        Text(allowsOneTime
                             ? "Bitte sende keine unnötigen personenbezogenen oder vertraulichen Daten. Du kannst alternativ nur den aktuellen Link freigeben."
                             : "Bitte sende keine unnötigen personenbezogenen oder vertraulichen Daten. Ohne Zustimmung bleibt der automatische Linkimport ausgeschaltet.")
                            .foregroundStyle(theme.muted)
                    } else {
                        Text("Bitte sende keine unnötigen personenbezogenen oder vertraulichen Daten. Deine Zustimmung gilt nur für diese Aktion. Ohne Zustimmung wird nichts übertragen.")
                            .foregroundStyle(theme.muted)
                    }
                    Link("Datenschutz", destination: AppLegalLinks.privacy)
                        .frame(minHeight: 44)
                        .accessibilityIdentifier("ai-consent.privacy")
                    if let serverPrivacy = AppLegalLinks.additionalServerPrivacyURL(for: request.server) {
                        Link("Datenschutz des Servers", destination: serverPrivacy).frame(minHeight: 44)
                    }
                    Button { approve(request.canRememberLinkImports) } label: {
                        Text(request.canRememberLinkImports ? "Zustimmen und automatisch importieren" : "Zustimmen und fortfahren")
                            .fontWeight(.semibold)
                            .frame(maxWidth: .infinity, minHeight: 48)
                    }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("ai-consent.approve")
                    if request.canRememberLinkImports, allowsOneTime {
                        Button("Nur diesen Link freigeben") { approve(false) }
                            .frame(maxWidth: .infinity, minHeight: 44)
                            .accessibilityIdentifier("ai-consent.approve-once")
                    }
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
