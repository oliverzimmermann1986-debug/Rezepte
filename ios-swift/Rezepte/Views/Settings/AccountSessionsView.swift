import SwiftUI

struct AccountSessionsView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var sessions: [AccountSession] = []
    @State private var selected: AccountSession?
    @State private var confirmAll = false
    @State private var isLoading = false
    @State private var isWorking = false
    @State private var errorMessage: String?

    var body: some View {
        List {
            Section {
                ForEach(sessions.sorted { $0.isCurrent && !$1.isCurrent }) { item in
                    VStack(alignment: .leading, spacing: 8) {
                        Label(item.isCurrent ? "Dieses Gerät" : item.clientLabel, systemImage: item.isCurrent ? "iphone" : "desktopcomputer")
                            .font(.headline)
                        if item.isCurrent { Text(item.clientLabel).font(.caption).foregroundStyle(theme.muted) }
                        Text("Zuletzt aktiv: \(accountDate(item.lastSeenAt))").font(.caption)
                        Text("Angemeldet: \(accountDate(item.createdAt))").font(.caption).foregroundStyle(theme.muted)
                        Text("Gültig bis: \(accountDate(item.expiresAt))").font(.caption).foregroundStyle(theme.muted)
                        Button(item.isCurrent ? "Dieses Gerät abmelden" : "Gerät abmelden", role: .destructive) { selected = item }
                            .disabled(isWorking || isLoading)
                    }
                    .padding(.vertical, 4)
                }
                if sessions.isEmpty && !isLoading { Text("Keine aktiven Sitzungen.").foregroundStyle(theme.muted) }
            } footer: { Text("Die Gerätenamen stammen aus der Anmeldung und können ähnlich aussehen.") }
            Section {
                Button("Alle Geräte abmelden", role: .destructive) { confirmAll = true }
                    .disabled(isWorking || isLoading || sessions.isEmpty)
            }
            if isLoading || isWorking { ProgressView() }
            if let errorMessage {
                Section {
                    Text(errorMessage).foregroundStyle(theme.danger)
                    Button("Erneut laden") { Task { await load() } }.disabled(isWorking)
                }
            }
        }
        .navigationTitle("Angemeldete Geräte")
        .scrollContentBackground(.hidden)
        .background(theme.background)
        .task { await load() }
        .refreshable { await load() }
        .confirmationDialog("Gerät abmelden?", isPresented: Binding(get: { selected != nil }, set: { if !$0 { selected = nil } }), titleVisibility: .visible) {
            if let selected {
                Button("Abmelden", role: .destructive) { let item = selected; Task { await revoke(item) } }
            }
            Button("Abbrechen", role: .cancel) { selected = nil }
        } message: { Text(selected?.isCurrent == true ? "Du musst dich auf diesem Gerät erneut anmelden." : "Diese Sitzung verliert sofort den Zugriff.") }
        .confirmationDialog("Alle Geräte abmelden?", isPresented: $confirmAll, titleVisibility: .visible) {
            Button("Alle abmelden", role: .destructive) { Task { await revokeAll() } }
            Button("Abbrechen", role: .cancel) {}
        } message: { Text("Auch dieses Gerät wird abgemeldet.") }
    }

    private func load(allowDuringMutation: Bool = false) async {
        guard !isLoading, !isWorking || allowDuringMutation else { return }
        let identity = session.identity
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let result = try await session.api.accountSessions()
            guard identity == session.identity else { return }
            sessions = result.sessions
        } catch { errorMessage = accountErrorMessage(error, session: session, identity: identity) }
    }

    private func revoke(_ item: AccountSession) async {
        guard !isWorking, !isLoading else { return }
        let identity = session.identity
        isWorking = true
        errorMessage = nil
        defer { isWorking = false }
        do {
            _ = try await session.api.revokeSession(id: item.id)
            guard identity == session.identity else { return }
            if item.isCurrent { session.signOut() } else { await load(allowDuringMutation: true) }
        } catch { errorMessage = accountErrorMessage(error, session: session, identity: identity) }
    }

    private func revokeAll() async {
        guard !isWorking, !isLoading else { return }
        let identity = session.identity
        isWorking = true
        errorMessage = nil
        defer { isWorking = false }
        do { try await session.logOutEverywhere() }
        catch { errorMessage = accountErrorMessage(error, session: session, identity: identity) }
    }
}
