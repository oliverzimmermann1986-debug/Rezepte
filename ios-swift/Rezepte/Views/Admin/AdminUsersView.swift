import SwiftUI

struct AdminUsersView: View {
    private struct Editor: Identifiable {
        let user: AdminUser?
        var id: Int { user?.id ?? -1 }
    }
    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var users: [AdminUser] = []
    @State private var search = ""
    @State private var editor: Editor?
    @State private var isLoading = false
    @State private var errorMessage: String?

    var body: some View {
        Group {
            if session.fullAccess {
                List {
                    ForEach(users.filter { search.isEmpty || $0.username.localizedCaseInsensitiveContains(search) }) { user in
                        Button { editor = Editor(user: user) } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(user.username).font(.headline).foregroundStyle(theme.ink)
                                    Text("\(user.role.title) · \(user.disabled == true ? "Deaktiviert" : "Aktiv")")
                                        .font(.caption).foregroundStyle(theme.muted)
                                }
                                Spacer()
                                Image(systemName: "chevron.right").font(.caption).foregroundStyle(theme.muted)
                            }
                        }
                    }
                    if isLoading { ProgressView("Benutzer werden geladen …") }
                    if let errorMessage {
                        Section {
                            Text(errorMessage).foregroundStyle(theme.danger)
                            Button("Erneut laden") { Task { await load() } }
                        }
                    }
                }
                .searchable(text: $search, prompt: "Benutzer suchen")
                .refreshable { await load() }
            } else {
                ContentUnavailableView("Administration erforderlich", systemImage: "lock")
            }
        }
        .navigationTitle("Benutzer")
        .scrollContentBackground(.hidden)
        .background(theme.background)
        .toolbar {
            if session.fullAccess {
                ToolbarItem(placement: .primaryAction) {
                    Button("Benutzer anlegen", systemImage: "plus") { editor = Editor(user: nil) }
                }
            }
        }
        .task { await load() }
        .sheet(item: $editor) { value in
            AdminUserEditor(user: value.user) { await load() }
        }
    }

    private func load() async {
        guard session.fullAccess, !isLoading else { return }
        let identity = session.identity
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let response = try await session.api.users()
            guard identity == session.identity else { return }
            users = response.users
        } catch { errorMessage = accountErrorMessage(error, session: session, identity: identity) }
    }
}

private struct AdminUserEditor: View {
    let user: AdminUser?
    let onSaved: () async -> Void
    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.recipeTheme) private var theme
    @State private var username: String
    @State private var role: AccountRole
    @State private var disabled: Bool
    @State private var password = ""
    @State private var confirmation = ""
    @State private var isWorking = false
    @State private var confirmDelete = false
    @State private var confirmRevoke = false
    @State private var errorMessage: String?
    @State private var successMessage: String?

    init(user: AdminUser?, onSaved: @escaping () async -> Void) {
        self.user = user
        self.onSaved = onSaved
        _username = State(initialValue: user?.username ?? "")
        _role = State(initialValue: user?.role ?? .user)
        _disabled = State(initialValue: user?.disabled ?? false)
    }

    private var isSelf: Bool {
        guard let user else { return false }
        return user.id == session.userID || username == session.username
    }
    private var validUsername: Bool {
        user != nil || username.range(of: "^[A-Za-z0-9_.-]{3,32}$", options: .regularExpression) != nil
    }
    private var passwordValid: Bool {
        (user != nil && password.isEmpty && confirmation.isEmpty)
            || (AccountPasswordPolicy.accepts(password) && password == confirmation)
    }
    private var changed: Bool {
        guard let user else { return true }
        return role != user.role || disabled != (user.disabled ?? false) || !password.isEmpty
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Benutzerkonto") {
                    if user == nil {
                        TextField("Benutzername", text: $username)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                        Text("3–32 Zeichen: Buchstaben, Ziffern, Punkt, Unterstrich oder Bindestrich.")
                            .font(.caption).foregroundStyle(theme.muted)
                    } else {
                        LabeledContent("Benutzername", value: username)
                    }
                    Picker("Rolle", selection: $role) {
                        ForEach(AccountRole.allCases) { item in
                            Text(item.title).tag(item)
                        }
                    }
                    if user != nil { Toggle("Deaktiviert", isOn: $disabled).disabled(isSelf) }
                }
                Section(user == nil ? "Passwort" : "Passwort zurücksetzen") {
                    SecureField(user == nil ? "Passwort" : "Neues Passwort (optional)", text: $password).textContentType(.newPassword)
                    SecureField("Passwort wiederholen", text: $confirmation).textContentType(.newPassword)
                    Text(AccountPasswordPolicy.guidance).font(.caption).foregroundStyle(theme.muted)
                    if user != nil {
                        Text("Leer lassen, um das Passwort beizubehalten. Ein neues Passwort oder eine geänderte Rolle meldet die bisherigen Sitzungen ab.")
                            .font(.caption).foregroundStyle(theme.muted)
                    }
                }
                if let user {
                    Section("Sitzungen") {
                        LabeledContent("Letzte Anmeldung", value: accountDate(user.lastLoginAt))
                        LabeledContent("Konto erstellt", value: accountDate(user.createdAt))
                        Button("Alle Sitzungen abmelden", role: .destructive) { confirmRevoke = true }
                    }
                    Section {
                        Button("Benutzer löschen", role: .destructive) { confirmDelete = true }.disabled(isSelf)
                        if isSelf { Text("Dein eigenes Konto kannst du unter „Mein Konto“ löschen.").font(.caption).foregroundStyle(theme.muted) }
                    }
                }
                if let errorMessage { Section { Text(errorMessage).foregroundStyle(theme.danger) } }
                if let successMessage { Section { Text(successMessage).foregroundStyle(theme.success) } }
                if isWorking { ProgressView() }
            }
            .disabled(isWorking)
            .navigationTitle(user == nil ? "Benutzer anlegen" : username)
            .scrollContentBackground(.hidden)
            .background(theme.background)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Abbrechen") { dismiss() }.disabled(isWorking) }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Speichern") { Task { await save() } }
                        .disabled(isWorking || !validUsername || !passwordValid || !changed)
                }
            }
            .interactiveDismissDisabled(isWorking)
            .confirmationDialog("Benutzer \(username) löschen?", isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("Endgültig löschen", role: .destructive) { Task { await delete() } }
                Button("Abbrechen", role: .cancel) {}
            } message: { Text("Das Benutzerkonto wird endgültig entfernt. Der Server schützt den letzten Administrator und Haushalte mit Daten.") }
            .confirmationDialog("Alle Sitzungen von \(username) abmelden?", isPresented: $confirmRevoke, titleVisibility: .visible) {
                Button("Alle abmelden", role: .destructive) { Task { await revoke() } }
                Button("Abbrechen", role: .cancel) {}
            } message: { Text(isSelf ? "Auch du wirst auf diesem Gerät abgemeldet." : "Der Benutzer muss sich auf allen Geräten erneut anmelden.") }
        }
    }

    private func save() async {
        guard !isWorking, validUsername, passwordValid, changed else { return }
        let identity = session.identity
        isWorking = true
        errorMessage = nil
        defer { isWorking = false }
        do {
            if let user {
                _ = try await session.api.updateUser(id: user.id, patch: AdminUserPatch(
                    password: password.isEmpty ? nil : password,
                    disabled: disabled == (user.disabled ?? false) ? nil : disabled,
                    role: role == user.role ? nil : role))
            } else {
                _ = try await session.api.createUser(username: username, password: password, role: role)
            }
            guard session.identity == identity else { return }
            if isSelf, !password.isEmpty || role != user?.role {
                session.signOut()
                session.alertMessage = "Konto aktualisiert. Bitte erneut anmelden."
            } else {
                await onSaved()
                guard session.identity == identity else { return }
                dismiss()
            }
            password = ""; confirmation = ""
        } catch { errorMessage = accountErrorMessage(error, session: session, identity: identity) }
    }

    private func delete() async {
        guard let user, !isWorking, !isSelf else { return }
        let identity = session.identity
        isWorking = true
        errorMessage = nil
        defer { isWorking = false }
        do {
            _ = try await session.api.deleteUser(id: user.id)
            guard session.identity == identity else { return }
            await onSaved()
            guard session.identity == identity else { return }
            dismiss()
        } catch { errorMessage = accountErrorMessage(error, session: session, identity: identity) }
    }

    private func revoke() async {
        guard let user, !isWorking else { return }
        let identity = session.identity
        isWorking = true
        errorMessage = nil
        defer { isWorking = false }
        do {
            _ = try await session.api.revokeUserSessions(id: user.id)
            guard session.identity == identity else { return }
            if isSelf { session.signOut() } else { successMessage = "Alle Sitzungen wurden abgemeldet." }
        } catch { errorMessage = accountErrorMessage(error, session: session, identity: identity) }
    }
}
