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
                cloudflareCredentials: cloudflareCredentials
            )
            let session = try await api.sessionInfo()
            apply(session)
            if !readOnly { await drainSharedImports() }
        } catch {
            signOut()
        }
    }

    func signIn(
        server: String,
        username: String,
        password: String,
        cloudflareClientID: String,
        cloudflareClientSecret: String
    ) async throws {
        let cloudflareCredentials = try CloudflareAccessCredentials(
            clientID: cloudflareClientID,
            clientSecret: cloudflareClientSecret
        )
        try await api.configure(
            server: server,
            token: nil,
            cloudflareCredentials: cloudflareCredentials
        )
        let response = try await api.login(username: username, password: password)
        try await activate(
            server: server,
            token: response.token,
            cloudflareCredentials: cloudflareCredentials
        )
    }

    func signInAsGuest(
        server: String,
        cloudflareClientID: String,
        cloudflareClientSecret: String
    ) async throws {
        let cloudflareCredentials = try CloudflareAccessCredentials(
            clientID: cloudflareClientID,
            clientSecret: cloudflareClientSecret
        )
        try await api.configure(
            server: server,
            token: nil,
            cloudflareCredentials: cloudflareCredentials
        )
        let response = try await api.guestLogin()
        try await activate(
            server: server,
            token: response.token,
            cloudflareCredentials: cloudflareCredentials
        )
    }

    func signOut() {
        KeychainStore.delete(account: tokenAccount)
        username = ""
        fullAccess = false
        readOnly = false
        state = .signedOut
    }

    func refreshAccess() async {
        guard case .signedIn = state else { return }
        do {
            let session = try await api.sessionInfo()
            apply(session)
        } catch {
            handle(error)
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
        cloudflareCredentials: CloudflareAccessCredentials?
    ) async throws {
        try await api.configure(
            server: server,
            token: token,
            cloudflareCredentials: cloudflareCredentials
        )
        let activeSession = try await api.sessionInfo()
        try KeychainStore.save(token, account: tokenAccount)
        try saveCloudflareCredentials(cloudflareCredentials)
        defaults.set(
            server.trimmingCharacters(in: .whitespacesAndNewlines),
            forKey: serverKey
        )
        apply(activeSession)
        if !readOnly { await drainSharedImports() }
    }

    private func apply(_ session: SessionResponse) {
        username = session.username
        fullAccess = session.fullAccess ?? false
        readOnly = session.readOnly ?? false
        state = .signedIn
    }

    func handle(_ error: Error) {
        if let apiError = error as? APIError,
           case .unauthenticated = apiError {
            signOut()
        }
        alertMessage = error.localizedDescription
    }

    func drainSharedImports() async {
        guard case .signedIn = state, !readOnly else { return }
        let queued = SharedImportQueue.all()
        guard !queued.isEmpty else { return }
        var imported = 0
        for url in queued {
            do {
                _ = try await api.importURL(url)
                SharedImportQueue.remove(url)
                imported += 1
            } catch {
                alertMessage = "Ein geteilter Link konnte noch nicht importiert werden: \(error.localizedDescription)"
                break
            }
        }
        if imported > 0 {
            alertMessage = imported == 1
                ? "Der geteilte Rezeptlink wurde importiert."
                : "\(imported) geteilte Links wurden importiert."
        }
    }
}
