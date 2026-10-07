import SwiftUI

struct AccountIdentitiesView: View {
    let passwordEnabled: Bool
    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var account: AccountIdentities?
    @State private var isLoading = false
    @State private var workingProvider: IdentityProvider?
    @State private var unlinking: AccountIdentity?
    @State private var linking: IdentityProvider?
    @State private var errorMessage: String?

    var body: some View {
        List {
            Section("Passwort") {
                Label(passwordEnabled ? "Passwort eingerichtet" : "Kein Passwort eingerichtet", systemImage: "key")
                if !passwordEnabled {
                    Text("Du meldest dich über Apple oder Google an. Du kannst unter „Mein Konto“ zusätzlich ein Passwort einrichten.")
                        .font(.caption).foregroundStyle(theme.muted)
                }
            }
            if let account {
                Section("Verknüpfte Anmeldungen") {
                    ForEach(account.identities) { identity in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(identity.title).font(.headline)
                            if let email = identity.email { Text(email).font(.subheadline) }
                            Text("Verknüpft: \(accountDate(identity.linkedAt))").font(.caption).foregroundStyle(theme.muted)
                            if let provider = IdentityProvider(rawValue: identity.provider) {
                                if account.providers.contains(where: { $0.kind == provider && $0.enabled }) {
                                    Button("Anmeldung erneut bestätigen") { Task { await link(provider) } }
                                }
                                Button("Verknüpfung entfernen", role: .destructive) { unlinking = identity }
                                    .disabled(!passwordEnabled && account.identities.count <= 1)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                    if account.identities.isEmpty { Text("Noch keine Apple- oder Google-Anmeldung verknüpft.") }
                }
                let available = account.providers.filter { provider in
                    provider.enabled && provider.kind != nil && !account.identities.contains(where: { $0.provider == provider.id })
                }
                if !available.isEmpty {
                    Section("Anmeldung hinzufügen") {
                        ForEach(available) { provider in
                            if let kind = provider.kind {
                                Button("Mit \(kind.title) verknüpfen") {
                                    if passwordEnabled { linking = kind }
                                    else { Task { await link(kind) } }
                                }
                            }
                        }
                    }
                }
                Section {
                    Text("Mindestens ein Anmeldeverfahren bleibt erhalten. Für geschützte Änderungen ohne Passwort muss die Apple- oder Google-Anmeldung höchstens fünf Minuten zurückliegen.")
                        .font(.caption).foregroundStyle(theme.muted)
                }
            }
            if isLoading || workingProvider != nil { ProgressView("Anmeldung wird vorbereitet …") }
            if let errorMessage {
                Section {
                    Text(errorMessage).foregroundStyle(theme.danger)
                    Button("Erneut laden") { Task { await load() } }
                }
            }
        }
        .disabled(workingProvider != nil)
        .navigationTitle("Anmeldeverfahren")
        .scrollContentBackground(.hidden)
        .background(theme.background)
        .task { await load() }
        .refreshable { await load() }
        .sheet(item: $unlinking) { identity in
            if let provider = IdentityProvider(rawValue: identity.provider) {
                UnlinkIdentityView(provider: provider, passwordEnabled: passwordEnabled) { await load() }
            }
        }
        .sheet(item: $linking) { provider in LinkIdentityView(provider: provider) }
    }

    private func load() async {
        guard !isLoading else { return }
        let identity = session.identity
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let result = try await session.api.accountIdentities()
            guard identity == session.identity else { return }
            account = result
        } catch { errorMessage = accountErrorMessage(error, session: session, identity: identity) }
    }

    private func link(_ provider: IdentityProvider) async {
        guard workingProvider == nil else { return }
        let identity = session.identity
        workingProvider = provider
        errorMessage = nil
        defer { workingProvider = nil }
        do {
            try await session.linkProvider(provider)
            session.alertMessage = "Die Anmeldung mit \(provider.title) wurde bestätigt."
        } catch { errorMessage = accountErrorMessage(error, session: session, identity: identity) }
    }
}

private struct UnlinkIdentityView: View {
    let provider: IdentityProvider
    let passwordEnabled: Bool
    let onRemoved: () async -> Void
    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.recipeTheme) private var theme
    @State private var password = ""
    @State private var isWorking = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Mit \(provider.title) kannst du dich danach nicht mehr bei diesem Konto anmelden. Deine anderen Anmeldeverfahren bleiben erhalten.")
                    if passwordEnabled {
                        SecureField("Aktuelles Passwort", text: $password).textContentType(.password)
                    } else {
                        Text("Bestätige zuvor eine deiner Provider-Anmeldungen. Die Bestätigung gilt fünf Minuten.")
                    }
                }
                if let errorMessage { Section { Text(errorMessage).foregroundStyle(theme.danger) } }
                Button("Verknüpfung entfernen", role: .destructive) { Task { await remove() } }
                    .disabled(isWorking || (passwordEnabled && password.isEmpty))
                if isWorking { ProgressView() }
            }
            .navigationTitle("\(provider.title) entfernen")
            .scrollContentBackground(.hidden)
            .background(theme.background)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Abbrechen") { dismiss() }.disabled(isWorking) }
            }
            .interactiveDismissDisabled(isWorking)
        }
    }

    private func remove() async {
        guard !isWorking else { return }
        let identity = session.identity
        isWorking = true
        errorMessage = nil
        defer { isWorking = false }
        do {
            _ = try await session.api.unlinkIdentity(provider: provider, currentPassword: password)
            guard session.identity == identity else { return }
            password = ""
            await onRemoved()
            guard session.identity == identity else { return }
            dismiss()
        } catch { errorMessage = accountErrorMessage(error, session: session, identity: identity) }
    }
}

private struct LinkIdentityView: View {
    let provider: IdentityProvider
    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.recipeTheme) private var theme
    @State private var password = ""
    @State private var isWorking = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Bestätige dein aktuelles Passwort, um \(provider.title) als zusätzlichen Anmeldeweg mit deinem Konto zu verknüpfen.")
                    SecureField("Aktuelles Passwort", text: $password).textContentType(.password)
                }
                if let errorMessage { Section { Text(errorMessage).foregroundStyle(theme.danger) } }
                Button("Bestätigen & fortfahren") { Task { await link() } }
                    .disabled(password.isEmpty || isWorking)
                if isWorking { ProgressView() }
            }
            .disabled(isWorking)
            .navigationTitle("\(provider.title) verknüpfen")
            .scrollContentBackground(.hidden)
            .background(theme.background)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Abbrechen") { dismiss() }.disabled(isWorking) }
            }
            .interactiveDismissDisabled(isWorking)
        }
    }

    private func link() async {
        guard !isWorking, !password.isEmpty else { return }
        let identity = session.identity
        isWorking = true
        errorMessage = nil
        defer { isWorking = false }
        do {
            try await session.linkProvider(provider, currentPassword: password)
            password = ""
            session.alertMessage = "Die Anmeldung mit \(provider.title) wurde verknüpft."
        } catch { errorMessage = accountErrorMessage(error, session: session, identity: identity) }
    }
}
