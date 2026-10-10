import SwiftUI

struct LoginView: View {
    private enum LoginAction: Equatable {
        case account
        case guest
        case registration
        case provider(IdentityProvider)
    }

    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @Environment(\.scenePhase) private var scenePhase
    @State private var server = ""
    @State private var serverDraft = ""
    @State private var showingServerEditor = false
    @State private var username = ""
    @State private var password = ""
    @State private var creatingAccount = false
    @State private var passwordConfirmation = ""
    @State private var invitationInput = ""
    @State private var selectedInvitation: LoginServerSetup.Invitation?
    @State private var workingAction: LoginAction?
    @State private var errorMessage: String?
    @State private var providers: [AuthProvider] = []
    @State private var providerLoadFailed = false
    @State private var providerReloadID = UUID()
    @State private var didInitialize = false

    private var selectedServer: URL? { LoginServerSetup.serverURL(server) }

    private var hasUnconfirmedServer: Bool { showingServerEditor && serverDraft != server }

    private var canChangeServer: Bool {
        workingAction == nil && LoginServerSetup.canChangeServer(password: password, confirmation: passwordConfirmation)
    }

    private var registrationToken: String? {
        LoginServerSetup.registrationToken(input: invitationInput, server: server, selectedInvitation: selectedInvitation)
    }

    private var canUseServer: Bool { selectedServer != nil && !hasUnconfirmedServer && workingAction == nil }

    private var canSubmit: Bool {
        canUseServer
            && !username.trimmingCharacters(in: .whitespaces).isEmpty
            && !password.isEmpty
            && (!creatingAccount || (username.trimmingCharacters(in: .whitespacesAndNewlines).count >= 3
                && AccountPasswordPolicy.accepts(password) && password == passwordConfirmation && registrationToken != nil))
            && workingAction == nil
    }

    private var canBrowseAsGuest: Bool {
        canUseServer
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
                        Text(creatingAccount ? "Erstelle deinen Haushalt oder komm per Einladung dazu." : "Rezepte sammeln, gemeinsam planen und einkaufen – in deinem Haushalt.")
                            .foregroundStyle(.secondary)
                    }

                    serverSelection

                    VStack(alignment: .leading, spacing: 14) {
                        if creatingAccount { invitationSelection }
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
                            Text("Mindestens 10 Zeichen. Ohne Einladung erhältst du einen eigenen Haushalt.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .textFieldStyle(.roundedBorder)
                    .disabled(workingAction != nil)

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

                    if !providers.isEmpty {
                        VStack(spacing: 12) {
                            Text("Beim ersten Mal wird dein Konto erstellt. Danach meldest du dich mit demselben Anbieter an.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            ForEach(providers) { provider in
                                if let kind = provider.kind {
                                    Button { Task { await signInWithProvider(kind) } } label: {
                                        HStack {
                                            if workingAction == .provider(kind) { ProgressView() }
                                            if kind == .apple { Image(systemName: "apple.logo") }
                                            Text("Mit \(kind.title) fortfahren").fontWeight(.semibold)
                                        }
                                        .frame(maxWidth: .infinity, minHeight: 48)
                                    }
                                    .buttonStyle(.bordered)
                                    .disabled(!canUseServer || (creatingAccount && registrationToken == nil))
                                }
                            }
                        }
                    } else if providerLoadFailed {
                        Button("Weitere Anmeldeverfahren erneut laden") { providerReloadID = UUID() }
                            .font(.footnote)
                            .disabled(workingAction != nil)
                    }

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
                clearIdleError()
                if !didInitialize {
                    creatingAccount = session.consumeRegistrationRequest()
                    let reviewEnvironment = ProcessInfo.processInfo.environment
                    server = LoginServerSetup.initialServer(saved: session.savedServer, reviewEnvironment: reviewEnvironment)
                    serverDraft = server
                    if reviewEnvironment["APP_REVIEW_AUTOMATION"] == "1" {
                        username = reviewEnvironment["APP_REVIEW_USERNAME"] ?? ""
                        password = reviewEnvironment["APP_REVIEW_PASSWORD"] ?? ""
                    }
                    didInitialize = true
                }
            }
            .task(id: "\(server)|\(providerReloadID)") { await loadProviders() }
            .onChange(of: scenePhase) { previous, current in
                // Clear the old foreground's error before suspending. An
                // in-flight action may still report a new error while inactive;
                // returning to the app must not erase that result.
                if previous == .active && current != .active {
                    clearIdleError()
                }
            }
            .onChange(of: [server, username, password, passwordConfirmation, invitationInput]) { _, _ in
                clearIdleError()
            }
        }
    }

    private var serverSelection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(server == LoginServerSetup.defaultServer ? "Rezeptregal-Dienst" : "Dein Server")
                .font(.headline)
            if let selectedServer, let host = selectedServer.host {
                HStack {
                    Image(systemName: "network")
                    TextField("Serveradresse", text: .constant(selectedServer.port.map { "\(host):\($0)" } ?? host))
                        .textFieldStyle(.plain)
                        .disabled(true)
                        .accessibilityLabel("Gewählter Server")
                        .accessibilityIdentifier("review.server")
                }
                .font(.subheadline)
                .foregroundStyle(theme.ink)
                if !selectedServer.path.isEmpty {
                    Text("Bereich: \(selectedServer.path)").font(.caption).foregroundStyle(.secondary)
                }
            } else {
                Text("Bitte prüfe die gespeicherte Serveradresse.")
                    .font(.subheadline).foregroundStyle(theme.warning)
            }
            if showingServerEditor {
                TextField("HTTPS-Adresse deines Servers", text: $serverDraft)
                    .textContentType(.URL)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textFieldStyle(.roundedBorder)
                    .accessibilityLabel("Serveradresse")
                    .accessibilityIdentifier("login.serverAddress")
                    .disabled(!canChangeServer)
                Text("Die Adresse erhältst du von der Person, die deinen Server betreibt. Mit einem Einladungslink kannst du sie beim Erstellen eines Kontos übernehmen.")
                    .font(.footnote).foregroundStyle(.secondary)
                if !serverDraft.isEmpty && LoginServerSetup.serverURL(serverDraft) == nil {
                    Text("Bitte eine HTTPS-Adresse ohne Zugangsdaten, Fragezeichen oder # eingeben.")
                        .font(.footnote).foregroundStyle(theme.warning)
                }
                HStack {
                    Button("Adresse verwenden") { selectServerDraft() }
                        .disabled(!canChangeServer || LoginServerSetup.serverURL(serverDraft) == nil)
                    Spacer()
                    Button("Abbrechen") {
                        serverDraft = server
                        showingServerEditor = false
                    }
                    .disabled(workingAction != nil)
                }
                .frame(minHeight: 44)
                if !canChangeServer && workingAction == nil { clearCredentialsButton }
            } else {
                if server == LoginServerSetup.defaultServer {
                    Text("Diese Adresse ist bereits eingerichtet. Du kannst dich direkt anmelden oder ein Konto erstellen.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Button("Anderen Server verwenden") {
                    serverDraft = server
                    showingServerEditor = true
                }
                .frame(minHeight: 44)
                .disabled(workingAction != nil)
            }
        }
    }

    private var invitationSelection: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField("Einladungslink oder Code (optional)", text: $invitationInput)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .accessibilityIdentifier("login.invitation")
            switch LoginServerSetup.invitation(invitationInput) {
            case .empty:
                Text("Schon eingeladen? Füge zuerst den Link ein, um den gemeinsamen Haushalt zu verwenden.")
                    .font(.footnote).foregroundStyle(.secondary)
            case .code:
                Text("Der Einladungscode gilt für den oben gewählten Server.")
                    .font(.footnote).foregroundStyle(.secondary)
            case let .link(invitation):
                if registrationToken != nil {
                    Label("Einladung ausgewählt · \(invitation.host)", systemImage: "checkmark.circle")
                        .font(.footnote)
                } else {
                    Text("Diese Einladung gehört zu \(invitation.host). Verwende sie nur, wenn du diesen Server kennst.")
                        .font(.footnote).foregroundStyle(.secondary)
                    Button("Einladungsserver verwenden") { selectInvitation(invitation) }
                        .frame(minHeight: 44)
                        .disabled(!canChangeServer)
                        .accessibilityIdentifier("login.useInvitation")
                    if !canChangeServer && workingAction == nil { clearCredentialsButton }
                }
            case .invalid:
                Text("Bitte den vollständigen HTTPS-Einladungslink oder den Einladungscode einfügen.")
                    .font(.footnote).foregroundStyle(theme.warning)
            }
        }
    }

    private var clearCredentialsButton: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Zum Serverwechsel zuerst die eingegebenen Zugangsdaten löschen.")
                .font(.footnote).foregroundStyle(.secondary)
            Button("Zugangsdaten löschen") {
                username = ""
                password = ""
                passwordConfirmation = ""
            }
            .frame(minHeight: 44)
        }
    }

    private func selectServerDraft() {
        guard canChangeServer, let url = LoginServerSetup.serverURL(serverDraft) else { return }
        server = url.absoluteString
        serverDraft = server
        selectedInvitation = nil
        showingServerEditor = false
        clearIdleError()
    }

    private func selectInvitation(_ invitation: LoginServerSetup.Invitation) {
        guard canChangeServer, LoginServerSetup.invitation(invitationInput) == .link(invitation) else { return }
        server = invitation.server.absoluteString
        serverDraft = server
        selectedInvitation = invitation
        showingServerEditor = false
        clearIdleError()
    }

    private func clearIdleError() {
        guard workingAction == nil else { return }
        errorMessage = nil
    }

    private func loadProviders() async {
        providers = []
        providerLoadFailed = false
        let requestedServer = server
        guard LoginServerSetup.serverURL(requestedServer) != nil else { return }
        do {
            try await Task.sleep(for: .milliseconds(350))
            // Discovery must not replace the active SessionStore API configuration.
            let discovery = APIClient()
            try await discovery.configure(server: requestedServer, token: nil)
            let response = try await discovery.authProviders()
            try Task.checkCancellation()
            guard server == requestedServer else { return }
            providers = response.available
        } catch is CancellationError {
        } catch {
            guard !Task.isCancelled, server == requestedServer else { return }
            if let error = error as? APIError, case .server(404, _) = error { return }
            providerLoadFailed = true
        }
    }

    private func signInWithProvider(_ provider: IdentityProvider) async {
        guard canUseServer, (!creatingAccount || registrationToken != nil),
              providers.contains(where: { $0.kind == provider && $0.enabled }) else { return }
        workingAction = .provider(provider)
        errorMessage = nil
        defer { workingAction = nil }
        do {
            try await session.signInWithProvider(provider, server: server,
                                                  invitationToken: creatingAccount ? (registrationToken ?? "") : "")
        } catch is CancellationError {
        } catch {
            if let error = error as? APIError, case .sessionChanged = error { return }
            errorMessage = error.localizedDescription
        }
    }

    private func submit() async {
        if creatingAccount { await register() } else { await signIn() }
    }

    private func register() async {
        guard canSubmit, let invitationToken = registrationToken else { return }
        workingAction = .registration
        errorMessage = nil
        defer { workingAction = nil }
        do {
            try await session.register(
                server: server, username: username.trimmingCharacters(in: .whitespacesAndNewlines),
                password: password, invitationToken: invitationToken
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
