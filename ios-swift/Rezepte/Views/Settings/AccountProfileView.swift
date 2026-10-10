import SwiftUI

@MainActor
func accountErrorMessage(_ error: Error, session: SessionStore, identity: UUID) -> String? {
    guard session.identity == identity, !(error is CancellationError) else { return nil }
    if let error = error as? APIError {
        if case .sessionChanged = error { return nil }
        if case .unauthenticated = error { session.handle(error); return nil }
    }
    return error.localizedDescription
}

func accountDate(_ timestamp: Double?) -> String {
    guard let timestamp, timestamp.isFinite else { return "Noch nicht" }
    return Date(timeIntervalSince1970: timestamp).formatted(date: .abbreviated, time: .shortened)
}

struct AccountProfileView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var profile: AccountProfile?
    @State private var errorMessage: String?
    @State private var isLoading = false

    var body: some View {
        List {
            if let profile {
                Section("Profil") {
                    LabeledContent("Benutzername", value: profile.username)
                    LabeledContent("Rolle", value: profile.role.title)
                    LabeledContent("Konto erstellt", value: accountDate(profile.createdAt))
                    LabeledContent("Letzte Anmeldung", value: accountDate(profile.lastLoginAt))
                }
                Section("Anmeldung & Sicherheit") {
                    NavigationLink(profile.passwordEnabled ? "Passwort ändern" : "Passwort einrichten") {
                        AccountPasswordView(passwordEnabled: profile.passwordEnabled)
                    }
                    NavigationLink("Apple, Google & Anmeldeverfahren") {
                        AccountIdentitiesView(passwordEnabled: profile.passwordEnabled)
                    }
                    NavigationLink("Angemeldete Geräte") { AccountSessionsView() }
                }
                if !session.readOnly, session.supports("household-invitations-v1") {
                    Section("Haushalt") {
                        NavigationLink("Mein Haushalt & Einladungen") { HouseholdAccountView() }
                    }
                }
                Section {
                    Button("Abmelden", role: .destructive) { Task { await session.logOut() } }
                        .disabled(session.isEndingSession)
                    NavigationLink { AccountDeletionView(passwordEnabled: profile.passwordEnabled) } label: {
                        Text("Konto löschen").foregroundStyle(theme.danger)
                    }
                }
            }
            if isLoading { ProgressView("Konto wird geladen …") }
            if let errorMessage {
                Section {
                    Text(errorMessage).foregroundStyle(theme.danger)
                    Button("Erneut laden") { Task { await load() } }
                }
            }
        }
        .navigationTitle("Mein Konto")
        .scrollContentBackground(.hidden)
        .background(theme.background)
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        guard !isLoading else { return }
        let identity = session.identity
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let result = try await session.api.accountProfile()
            guard session.identity == identity else { return }
            profile = result
        } catch { errorMessage = accountErrorMessage(error, session: session, identity: identity) }
    }
}

struct AccountPasswordView: View {
    let passwordEnabled: Bool
    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var currentPassword = ""
    @State private var newPassword = ""
    @State private var confirmation = ""
    @State private var isSaving = false
    @State private var errorMessage: String?

    private var canSave: Bool {
        (!passwordEnabled || !currentPassword.isEmpty) && AccountPasswordPolicy.accepts(newPassword)
            && newPassword == confirmation && !isSaving
    }

    var body: some View {
        Form {
            Section {
                if passwordEnabled {
                    SecureField("Aktuelles Passwort", text: $currentPassword).textContentType(.password)
                } else {
                    Text("Bestätige zuerst deine Apple- oder Google-Anmeldung unter „Anmeldeverfahren“. Die Bestätigung gilt fünf Minuten.")
                }
                SecureField("Neues Passwort", text: $newPassword).textContentType(.newPassword)
                SecureField("Neues Passwort wiederholen", text: $confirmation).textContentType(.newPassword)
            } footer: {
                Text(AccountPasswordPolicy.guidance + " Anschließend werden alle Geräte abgemeldet.")
            }
            if let errorMessage { Section { Text(errorMessage).foregroundStyle(theme.danger) } }
            Section {
                Button { Task { await save() } } label: {
                    if isSaving { ProgressView() } else { Text(passwordEnabled ? "Passwort ändern" : "Passwort einrichten") }
                }
                .disabled(!canSave)
            }
        }
        .disabled(isSaving)
        .navigationTitle(passwordEnabled ? "Passwort ändern" : "Passwort einrichten")
        .scrollContentBackground(.hidden)
        .background(theme.background)
    }

    private func save() async {
        guard canSave else { return }
        let identity = session.identity
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }
        do {
            _ = try await session.api.changePassword(current: currentPassword, new: newPassword)
            guard session.identity == identity else { return }
            currentPassword = ""; newPassword = ""; confirmation = ""
            session.signOut()
            session.alertMessage = "Passwort gespeichert. Bitte erneut anmelden."
        } catch { errorMessage = accountErrorMessage(error, session: session, identity: identity) }
    }
}

struct AccountDeletionView: View {
    let passwordEnabled: Bool
    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var password = ""
    @State private var confirmDeletion = false
    @State private var deleteHousehold = false
    @State private var deletionPhrase = ""
    @State private var household: HouseholdAccount?
    @State private var isDeleting = false
    @State private var errorMessage: String?

    var body: some View {
        Form {
            Section {
                Text("Dein Benutzerkonto und deine Anmeldungen werden endgültig gelöscht. Gemeinsame Haushaltsdaten bleiben für weitere Mitglieder erhalten.")
                Text("Als letztes Mitglied kannst du deinen privaten Haushalt ausdrücklich mitlöschen. Der letzte aktive Serveradministrator muss zuerst einen weiteren Administrator benennen.")
                if household?.members.count == 1 {
                    Toggle("Privaten Haushalt mitlöschen", isOn: $deleteHousehold)
                }
                if deleteHousehold {
                    Text("Private Rezepte, Bilder, PDFs, Einkauf, Wochenplan, Kochbücher und Kochhistorie werden dauerhaft entfernt. Freigaben und Einladungen verfallen. Globale Rezepte und fremde Haushalte bleiben erhalten. Bei laufenden Importen bitte nach deren Abschluss erneut bestätigen.")
                    TextField("HAUSHALT LÖSCHEN", text: $deletionPhrase)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .accessibilityLabel("Zur Bestätigung HAUSHALT LÖSCHEN eingeben")
                }
                if passwordEnabled {
                    SecureField("Aktuelles Passwort", text: $password).textContentType(.password)
                } else {
                    Text("Bestätige deine Apple- oder Google-Anmeldung zuvor unter „Anmeldeverfahren“. Die Bestätigung gilt fünf Minuten.")
                }
            }
            if let errorMessage { Section { Text(errorMessage).foregroundStyle(theme.danger) } }
            Section {
                Button(deleteHousehold ? "Konto und Haushalt endgültig löschen" : "Konto endgültig löschen", role: .destructive) { confirmDeletion = true }
                    .disabled(isDeleting || household == nil || (passwordEnabled && password.isEmpty)
                              || (deleteHousehold && deletionPhrase != "HAUSHALT LÖSCHEN"))
                if isDeleting { ProgressView("Konto wird gelöscht …") }
            }
        }
        .disabled(isDeleting)
        .navigationTitle("Konto löschen")
        .scrollContentBackground(.hidden)
        .background(theme.background)
        .task {
            let identity = session.identity
            do {
                let result = try await session.api.account()
                guard session.identity == identity else { return }
                household = result
            } catch { errorMessage = accountErrorMessage(error, session: session, identity: identity) }
        }
        .confirmationDialog("Konto \(session.username) endgültig löschen?", isPresented: $confirmDeletion, titleVisibility: .visible) {
            Button("Endgültig löschen", role: .destructive) { Task { await delete() } }
            Button("Abbrechen", role: .cancel) {}
        } message: { Text(deleteHousehold ? "Auch dein gesamter privater Haushalt wird dauerhaft gelöscht. Diese Aktion kann nicht rückgängig gemacht werden." : "Diese Aktion kann nicht rückgängig gemacht werden.") }
    }

    private func delete() async {
        guard !isDeleting else { return }
        let identity = session.identity
        isDeleting = true
        errorMessage = nil
        defer { isDeleting = false }
        do {
            let result = try await session.api.deleteAccount(currentPassword: password,
                                                            deleteHousehold: deleteHousehold,
                                                            confirmation: deleteHousehold ? deletionPhrase : "")
            guard session.identity == identity else { return }
            password = ""
            for url in SharedImportQueue.all() { SharedImportQueue.remove(url) }
            session.signOut()
            session.alertMessage = result.message ?? "Dein Konto wurde gelöscht."
        } catch { errorMessage = accountErrorMessage(error, session: session, identity: identity) }
    }
}
