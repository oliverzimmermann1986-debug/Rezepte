import SwiftUI

struct LoginView: View {
    private enum LoginAction: Equatable {
        case account
        case guest
        case registration
    }

    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var server = ""
    @State private var username = ""
    @State private var password = ""
    @State private var creatingAccount = false
    @State private var passwordConfirmation = ""
    @State private var invitationInput = ""
    @State private var workingAction: LoginAction?
    @State private var errorMessage: String?

    private var canSubmit: Bool {
        !server.trimmingCharacters(in: .whitespaces).isEmpty
            && !username.trimmingCharacters(in: .whitespaces).isEmpty
            && !password.isEmpty
            && (!creatingAccount || (username.trimmingCharacters(in: .whitespacesAndNewlines).count >= 3
                && password.count >= 10 && password.utf8.count <= 72 && password == passwordConfirmation))
            && workingAction == nil
    }

    private var canBrowseAsGuest: Bool {
        !server.trimmingCharacters(in: .whitespaces).isEmpty
            && workingAction == nil
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    VStack(alignment: .leading, spacing: 10) {
                        Image(systemName: "fork.knife.circle.fill")
                            .font(.system(size: 58))
                            .foregroundStyle(theme.accent)
                        Text("Quellen rein.\nLieblingsessen raus.")
                            .font(.largeTitle.bold())
                            .foregroundStyle(theme.ink)
                        Text(creatingAccount ? "Erstelle deinen eigenen Haushalt oder nimm eine Einladung an." : "Melde dich bei deiner Quellenküche an.")
                            .foregroundStyle(.secondary)
                    }

                    VStack(spacing: 14) {
                        TextField("https://rezepte.example.de", text: $server)
                            .textContentType(.URL)
                            .keyboardType(.URL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .submitLabel(.next)
                            .accessibilityIdentifier("review.server")
                        TextField("Benutzername", text: $username)
                            .textContentType(.username)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .accessibilityIdentifier("review.username")
                        SecureField("Passwort", text: $password)
                            .textContentType(creatingAccount ? .newPassword : .password)
                            .submitLabel(.go)
                            .onSubmit { Task { await submit() } }
                            .accessibilityIdentifier("review.password")
                        if creatingAccount {
                            SecureField("Passwort wiederholen", text: $passwordConfirmation)
                                .textContentType(.newPassword)
                            TextField("Einladungslink oder Code (optional)", text: $invitationInput)
                                .textInputAutocapitalization(.never)
                                .autocorrectionDisabled()
                            Text("Mindestens 10 Zeichen. Ohne Einladung erhältst du einen eigenen Haushalt; mit Einladung teilst du den Haushalt der einladenden Person.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .textFieldStyle(.roundedBorder)

                    if let errorMessage {
                        Label(errorMessage, systemImage: "exclamationmark.circle.fill")
                            .font(.subheadline)
                            .foregroundStyle(.red)
                    }

                    Button {
                        Task { await submit() }
                    } label: {
                        HStack {
                            if workingAction == .account || workingAction == .registration { ProgressView() }
                            Text(creatingAccount
                                ? (workingAction == .registration ? "Konto wird erstellt …" : "Konto erstellen")
                                : (workingAction == .account ? "Anmeldung läuft …" : "Anmelden"))
                                .fontWeight(.semibold)
                        }
                        .frame(maxWidth: .infinity, minHeight: 48)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(theme.accent)
                    .foregroundStyle(theme.ink)
                    .disabled(!canSubmit)

                    Button(creatingAccount ? "Mit bestehendem Konto anmelden" : "Konto erstellen") {
                        creatingAccount.toggle()
                        errorMessage = nil
                        passwordConfirmation = ""
                    }
                    .disabled(workingAction != nil)

                    VStack(spacing: 10) {
                        HStack {
                            Divider()
                            Text("oder")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Divider()
                        }

                        Button {
                            Task { await signInAsGuest() }
                        } label: {
                            HStack {
                                if workingAction == .guest { ProgressView() }
                                Label(
                                    workingAction == .guest ? "Gastzugang wird geöffnet …" : "Als Gast ansehen",
                                    systemImage: "eye"
                                )
                                .fontWeight(.semibold)
                            }
                            .frame(maxWidth: .infinity, minHeight: 48)
                        }
                        .buttonStyle(.bordered)
                        .disabled(!canBrowseAsGuest)

                        Text("Im Gastzugang kannst du Rezepte suchen und lesen. Hinzufügen, Bearbeiten, Favoriten, Einkauf und Wochenplanung sind gesperrt.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }

                    Label(
                        "Das Passwort wird nicht gespeichert. Dein Sitzungsschlüssel liegt geschützt im iOS-Schlüsselbund.",
                        systemImage: "lock.shield"
                    )
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                }
                .padding(24)
            }
            .background(theme.background)
            .onAppear {
                creatingAccount = session.registrationRequested
                let reviewEnvironment = ProcessInfo.processInfo.environment
                if reviewEnvironment["APP_REVIEW_AUTOMATION"] == "1" {
                    if server.isEmpty {
                        server = reviewEnvironment["APP_REVIEW_SERVER"] ?? session.savedServer
                    }
                    if username.isEmpty {
                        username = reviewEnvironment["APP_REVIEW_USERNAME"] ?? ""
                    }
                    if password.isEmpty {
                        password = reviewEnvironment["APP_REVIEW_PASSWORD"] ?? ""
                    }
                } else if server.isEmpty {
                    server = session.savedServer
                }
            }
        }
    }

    private func submit() async {
        if creatingAccount { await register() } else { await signIn() }
    }

    private func register() async {
        guard canSubmit else { return }
        workingAction = .registration
        errorMessage = nil
        defer { workingAction = nil }
        do {
            try await session.register(
                server: server, username: username.trimmingCharacters(in: .whitespacesAndNewlines),
                password: password, invitationToken: invitationInput
            )
            password = ""
            passwordConfirmation = ""
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func signIn() async {
        guard canSubmit else { return }
        workingAction = .account
        errorMessage = nil
        defer { workingAction = nil }
        do {
            try await session.signIn(
                server: server,
                username: username,
                password: password
            )
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func signInAsGuest() async {
        guard canBrowseAsGuest else { return }
        workingAction = .guest
        errorMessage = nil
        defer { workingAction = nil }
        do {
            try await session.signInAsGuest(server: server)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
