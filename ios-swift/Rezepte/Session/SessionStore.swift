import Combine
import Foundation
import OSLog

@MainActor
final class SessionStore: ObservableObject {
    enum State {
        case checking
        case signedOut
        case signedIn
    }

    @Published private(set) var state: State = .checking
    @Published private(set) var username = ""
    @Published private(set) var userID: Int?
    @Published private(set) var role: AccountRole = .guest
    @Published private(set) var passwordEnabled = false
    @Published private(set) var fullAccess = false
    @Published private(set) var readOnly = false
    @Published private(set) var serverVersion = ""
    @Published private(set) var serverCapabilities: Set<String> = []
    @Published private(set) var compatibilityWarning: String?
    @Published private(set) var identity = UUID()
    @Published private(set) var registrationRequested = false
    @Published private(set) var isEndingSession = false
    @Published var alertMessage: String?

    let api: APIClient
    private let defaults: UserDefaults
    private let persistence: LocalSessionPersistence
    private let webAuthentication: any NativeAuthenticating
    private let serverKey = "server-url"

    init(api: APIClient = APIClient(), defaults: UserDefaults = .standard,
         persistence: LocalSessionPersistence? = nil, webAuthentication: (any NativeAuthenticating)? = nil) {
        self.api = api
        self.defaults = defaults
        self.persistence = persistence ?? LocalSessionPersistence(defaults: defaults)
        self.webAuthentication = webAuthentication ?? NativeWebAuthentication()
    }

    var savedServer: String {
        defaults.string(forKey: serverKey) ?? ""
    }

    func restore() async {
        removeLegacyAccessCredentials()
        let expectedIdentity = identity
        guard !savedServer.isEmpty,
              let token = persistence.restoredToken() else {
            state = .signedOut
            return
        }
        do {
            try await api.configure(
                server: savedServer,
                token: token,
                sessionID: expectedIdentity
            )
            let session = try await api.sessionInfo()
            guard identity == expectedIdentity else { return }
            apply(session)
            await refreshSystemInfo()
            guard identity == expectedIdentity else { return }
            if !readOnly { await drainSharedImports() }
        } catch {
            if identity == expectedIdentity { signOut() }
        }
    }

    func signIn(
        server: String,
        username: String,
        password: String
    ) async throws {
        identity = UUID()
        let expectedIdentity = identity
        try await api.configure(
            server: server,
            token: nil,
            sessionID: expectedIdentity
        )
        let response = try await api.login(username: username, password: password)
        try await activate(
            server: server,
            token: response.token,
            expectedIdentity: expectedIdentity
        )
    }

    func signInAsGuest(server: String) async throws {
        identity = UUID()
        let expectedIdentity = identity
        try await api.configure(
            server: server,
            token: nil,
            sessionID: expectedIdentity
        )
        let response = try await api.guestLogin()
        try await activate(
            server: server,
            token: response.token,
            expectedIdentity: expectedIdentity
        )
    }

    func register(
        server: String, username: String, password: String, invitationToken: String
    ) async throws {
        identity = UUID()
        let expectedIdentity = identity
        try await api.configure(server: server, token: nil, sessionID: expectedIdentity)
        let response = try await api.register(username: username, password: password, invitationToken: invitationToken)
        try await activate(server: server, token: response.token, expectedIdentity: expectedIdentity)
    }

    func signOut() {
        webAuthentication.cancel()
        let previousIdentity = identity
        if !readOnly, !username.isEmpty {
            for url in SharedImportQueue.all() { SharedImportQueue.remove(url) }
        }
        identity = UUID()
        URLCache.shared.removeAllCachedResponses()
        Task { await api.clearAuthentication(ifSessionID: previousIdentity) }
        persistence.signOut()
        removeLegacyAccessCredentials()
        username = ""
        userID = nil
        role = .guest
        passwordEnabled = false
        fullAccess = false
        readOnly = false
        serverVersion = ""
        serverCapabilities = []
        compatibilityWarning = nil
        registrationRequested = false
        state = .signedOut
    }

    func startRegistration() {
        signOut()
        registrationRequested = true
    }

    func consumeRegistrationRequest() -> Bool {
        let requested = registrationRequested
        registrationRequested = false
        return requested
    }

    func logOut() async {
        guard case .signedIn = state, !isEndingSession else { return }
        let expectedIdentity = identity
        isEndingSession = true
        defer { isEndingSession = false }
        _ = try? await api.logout()
        if identity == expectedIdentity { signOut() }
    }

    func logOutEverywhere() async throws {
        guard case .signedIn = state, !isEndingSession else { return }
        let expectedIdentity = identity
        isEndingSession = true
        defer { isEndingSession = false }
        _ = try await api.logoutAll()
        guard identity == expectedIdentity else { throw APIError.sessionChanged }
        signOut()
    }

    func signInWithProvider(_ provider: IdentityProvider, server: String, invitationToken: String = "") async throws {
        identity = UUID()
        let expectedIdentity = identity
        try await api.configure(server: server, token: nil, sessionID: expectedIdentity)
        try await authenticateProvider(provider, intent: .login, server: server,
                                       invitationToken: invitationToken, expectedIdentity: expectedIdentity)
    }

    func linkProvider(_ provider: IdentityProvider, currentPassword: String? = nil) async throws {
        guard case .signedIn = state, !readOnly else { throw APIError.unauthenticated }
        try await authenticateProvider(provider, intent: .link, server: savedServer,
                                       invitationToken: "", expectedIdentity: identity, currentPassword: currentPassword)
    }

    private func authenticateProvider(_ provider: IdentityProvider, intent: NativeAuthIntent, server: String,
                                      invitationToken: String, expectedIdentity: UUID, currentPassword: String? = nil) async throws {
        let proof = try NativeAuthProof.make()
        let start = try await api.startNativeAuth(provider: provider, intent: intent,
                                                 challenge: proof.challenge, invitationToken: invitationToken,
                                                 currentPassword: currentPassword)
        try Task.checkCancellation()
        guard identity == expectedIdentity else { throw APIError.sessionChanged }
        guard !start.flowId.isEmpty else { throw NativeAuthError.invalidCallback }
        let callback = try await webAuthentication.authenticate(url: NativeAuthCallback.authorizationURL(start.authorizationUrl))
        try Task.checkCancellation()
        guard identity == expectedIdentity else { throw APIError.sessionChanged }
        let code = try NativeAuthCallback.code(from: callback, expectedFlow: start.flowId)
        let response = try await api.exchangeNativeAuth(code: code, verifier: proof.verifier)
        try Task.checkCancellation()
        guard identity == expectedIdentity else { throw APIError.sessionChanged }
        // Linking creates a new server session too; remount account-bound views.
        identity = UUID()
        let activatedIdentity = identity
        do {
            try await activate(server: server, token: response.token, expectedIdentity: activatedIdentity)
        } catch {
            if identity == activatedIdentity, intent == .link {
                signOut()
                alertMessage = error.localizedDescription
            }
            throw error
        }
    }

    func householdDidChange() async throws {
        guard let token = persistence.restoredToken() else { throw APIError.unauthenticated }
        identity = UUID()
        let expectedIdentity = identity
        try await api.configure(server: savedServer, token: token, sessionID: expectedIdentity)
        let current = try await api.sessionInfo()
        guard identity == expectedIdentity else { throw APIError.sessionChanged }
        apply(current)
    }

    func refreshAccess() async {
        // A locked keychain may reject cleanup during startup. Retry on each
        // foreground entry without discarding a genuine account session.
        removeLegacyAccessCredentials()
        guard case .signedIn = state else { return }
        let expectedIdentity = identity
        do {
            let session = try await api.sessionInfo()
            guard identity == expectedIdentity else { return }
            apply(session)
        } catch {
            if identity == expectedIdentity { handle(error) }
        }
    }

    private func removeLegacyAccessCredentials() {
        let completed = LegacyAccessMigration.removeCredentials(
            read: { KeychainStore.read(account: $0) },
            delete: { KeychainStore.delete(account: $0) }
        )
        if !completed {
            Logger(subsystem: "de.mausbaeren.rezepte", category: "session")
                .warning("Retired device credentials could not be fully removed; cleanup will retry.")
        }
    }

    private func activate(
        server: String,
        token: String,
        expectedIdentity: UUID
    ) async throws {
        guard identity == expectedIdentity else { throw APIError.sessionChanged }
        try await api.configure(
            server: server,
            token: token,
            sessionID: expectedIdentity
        )
        let activeSession = try await api.sessionInfo()
        guard identity == expectedIdentity else { throw APIError.sessionChanged }
        try persistence.activate(token)
        defaults.set(
            server.trimmingCharacters(in: .whitespacesAndNewlines),
            forKey: serverKey
        )
        apply(activeSession)
        await refreshSystemInfo()
        guard identity == expectedIdentity else { throw APIError.sessionChanged }
        if !readOnly { await drainSharedImports() }
    }

    private func apply(_ session: SessionResponse) {
        username = session.username
        userID = session.id
        role = session.effectiveRole
        fullAccess = role == .admin
        readOnly = role == .guest
        passwordEnabled = session.passwordEnabled ?? (role != .guest)
        state = .signedIn
    }

    func supports(_ capability: String) -> Bool {
        serverCapabilities.contains(capability)
    }

    private func refreshSystemInfo() async {
        let expectedIdentity = identity
        do {
            let info = try await api.systemInfo()
            guard identity == expectedIdentity else { return }
            serverVersion = info.version
            serverCapabilities = Set(info.capabilities)
            let required = Set([
                "shopping-categories",
                "recurring-shopping",
                "weekly-meal-plan"
            ])
            let missing = required.subtracting(serverCapabilities).sorted()
            compatibilityWarning = missing.isEmpty
                ? nil
                : "Der Server ist älter als diese App. Es fehlen: \(missing.joined(separator: ", "))."
        } catch {
            guard identity == expectedIdentity else { return }
            serverVersion = "unbekannt"
            serverCapabilities = []
            compatibilityWarning = "Serverversion konnte nicht geprüft werden. App und Server bitte gemeinsam aktualisieren."
        }
    }

    func handle(_ error: Error) {
        if error is CancellationError { return }
        if let apiError = error as? APIError, case .sessionChanged = apiError { return }
        if let apiError = error as? APIError,
           case .unauthenticated = apiError {
            signOut()
        }
        alertMessage = error.localizedDescription
    }

    func drainSharedImports() async {
        guard case .signedIn = state, !readOnly else { return }
        let expectedIdentity = identity
        let queued = SharedImportQueue.all()
        guard !queued.isEmpty else { return }
        var imported = 0
        var linked = 0
        for url in queued {
            guard identity == expectedIdentity, !readOnly else { return }
            do {
                let result = try await api.importURL(url)
                guard identity == expectedIdentity else { return }
                SharedImportQueue.remove(url)
                imported += 1
                if result.status == "linked_global" { linked += 1 }
            } catch {
                guard identity == expectedIdentity else { return }
                alertMessage = "Ein geteilter Link konnte noch nicht importiert werden: \(error.localizedDescription)"
                break
            }
        }
        if imported > 0 {
            alertMessage = imported == 1 && linked == 1
                ? "Das globale Rezept wurde in deinem Haushalt gespeichert. Kein erneuter Download."
                : imported == 1
                ? "Der geteilte Rezeptlink wurde importiert."
                : "\(imported) geteilte Links wurden importiert."
        }
    }
}
