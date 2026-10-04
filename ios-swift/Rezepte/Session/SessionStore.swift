import Combine
import Foundation

@MainActor
final class SessionStore: ObservableObject {
    enum State {
        case checking
        case signedOut
        case signedIn
    }

    @Published private(set) var state: State = .checking
    @Published private(set) var username = ""
    @Published private(set) var fullAccess = false
    @Published private(set) var readOnly = false
    @Published private(set) var serverVersion = ""
    @Published private(set) var serverCapabilities: Set<String> = []
    @Published private(set) var compatibilityWarning: String?
    @Published private(set) var identity = UUID()
    @Published private(set) var registrationRequested = false
    @Published private(set) var isEndingSession = false
    @Published var alertMessage: String?

    let api = APIClient()
    private let defaults = UserDefaults.standard
    private let tokenAccount = "api-token"
    private let cloudflareClientIDAccount = "cloudflare-client-id"
    private let cloudflareClientSecretAccount = "cloudflare-client-secret"
    private let serverKey = "server-url"

    var savedServer: String {
        defaults.string(forKey: serverKey) ?? ""
    }

    var savedCloudflareClientID: String {
        KeychainStore.read(account: cloudflareClientIDAccount) ?? ""
    }

    var savedCloudflareClientSecret: String {
        KeychainStore.read(account: cloudflareClientSecretAccount) ?? ""
    }

    func restore() async {
        let expectedIdentity = identity
        guard !savedServer.isEmpty,
              let token = KeychainStore.read(account: tokenAccount) else {
            state = .signedOut
            return
        }
        do {
            let cloudflareCredentials = try CloudflareAccessCredentials(
                clientID: savedCloudflareClientID,
                clientSecret: savedCloudflareClientSecret
            )
            try await api.configure(
                server: savedServer,
                token: token,
                cloudflareCredentials: cloudflareCredentials,
                sessionID: expectedIdentity
            )
            let session = try await api.sessionInfo()
            guard identity == expectedIdentity else { return }
            apply(session)
            await refreshSystemInfo()
            if !readOnly { await drainSharedImports() }
        } catch {
            if identity == expectedIdentity { signOut() }
        }
    }

    func signIn(
        server: String,
        username: String,
        password: String,
        cloudflareClientID: String,
        cloudflareClientSecret: String
    ) async throws {
        identity = UUID()
        let expectedIdentity = identity
        let cloudflareCredentials = try CloudflareAccessCredentials(
            clientID: cloudflareClientID,
            clientSecret: cloudflareClientSecret
        )
        try await api.configure(
            server: server,
            token: nil,
            cloudflareCredentials: cloudflareCredentials,
            sessionID: expectedIdentity
        )
        let response = try await api.login(username: username, password: password)
        try await activate(
            server: server,
            token: response.token,
            cloudflareCredentials: cloudflareCredentials,
            expectedIdentity: expectedIdentity
        )
    }

    func signInAsGuest(
        server: String,
        cloudflareClientID: String,
        cloudflareClientSecret: String
    ) async throws {
        identity = UUID()
        let expectedIdentity = identity
        let cloudflareCredentials = try CloudflareAccessCredentials(
            clientID: cloudflareClientID,
            clientSecret: cloudflareClientSecret
        )
        try await api.configure(
            server: server,
            token: nil,
            cloudflareCredentials: cloudflareCredentials,
            sessionID: expectedIdentity
        )
        let response = try await api.guestLogin()
        try await activate(
            server: server,
            token: response.token,
            cloudflareCredentials: cloudflareCredentials,
            expectedIdentity: expectedIdentity
        )
    }

    func register(
        server: String, username: String, password: String, invitationToken: String,
        cloudflareClientID: String, cloudflareClientSecret: String
    ) async throws {
        identity = UUID()
        let expectedIdentity = identity
        let cloudflare = try CloudflareAccessCredentials(clientID: cloudflareClientID, clientSecret: cloudflareClientSecret)
        try await api.configure(server: server, token: nil, cloudflareCredentials: cloudflare, sessionID: expectedIdentity)
        let response = try await api.register(username: username, password: password, invitationToken: invitationToken)
        try await activate(server: server, token: response.token, cloudflareCredentials: cloudflare, expectedIdentity: expectedIdentity)
    }

    func signOut() {
        let previousIdentity = identity
        if !readOnly, !username.isEmpty {
            for url in SharedImportQueue.all() { SharedImportQueue.remove(url) }
        }
        identity = UUID()
        URLCache.shared.removeAllCachedResponses()
        Task { await api.clearAuthentication(ifSessionID: previousIdentity) }
        KeychainStore.delete(account: tokenAccount)
        username = ""
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

    func logOut() async {
        guard case .signedIn = state, !isEndingSession else { return }
        let expectedIdentity = identity
        isEndingSession = true
        defer { isEndingSession = false }
        _ = try? await api.logout()
        if identity == expectedIdentity { signOut() }
    }

    func householdDidChange() async throws {
        guard let token = KeychainStore.read(account: tokenAccount) else { throw APIError.unauthenticated }
        let cloudflare = try CloudflareAccessCredentials(clientID: savedCloudflareClientID, clientSecret: savedCloudflareClientSecret)
        identity = UUID()
        let expectedIdentity = identity
        try await api.configure(server: savedServer, token: token, cloudflareCredentials: cloudflare, sessionID: expectedIdentity)
        let current = try await api.sessionInfo()
        guard identity == expectedIdentity else { throw APIError.sessionChanged }
        apply(current)
    }

    func refreshAccess() async {
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

    private func saveCloudflareCredentials(_ credentials: CloudflareAccessCredentials?) throws {
        guard let credentials else {
            KeychainStore.delete(account: cloudflareClientIDAccount)
            KeychainStore.delete(account: cloudflareClientSecretAccount)
            return
        }
        try KeychainStore.save(credentials.clientID, account: cloudflareClientIDAccount)
        try KeychainStore.save(credentials.clientSecret, account: cloudflareClientSecretAccount)
    }

    private func activate(
        server: String,
        token: String,
        cloudflareCredentials: CloudflareAccessCredentials?,
        expectedIdentity: UUID
    ) async throws {
        guard identity == expectedIdentity else { throw APIError.sessionChanged }
        try await api.configure(
            server: server,
            token: token,
            cloudflareCredentials: cloudflareCredentials,
            sessionID: expectedIdentity
        )
        let activeSession = try await api.sessionInfo()
        guard identity == expectedIdentity else { throw APIError.sessionChanged }
        try KeychainStore.save(token, account: tokenAccount)
        try saveCloudflareCredentials(cloudflareCredentials)
        defaults.set(
            server.trimmingCharacters(in: .whitespacesAndNewlines),
            forKey: serverKey
        )
        apply(activeSession)
        await refreshSystemInfo()
        if !readOnly { await drainSharedImports() }
    }

    private func apply(_ session: SessionResponse) {
        username = session.username
        fullAccess = session.fullAccess ?? false
        readOnly = session.readOnly ?? false
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
