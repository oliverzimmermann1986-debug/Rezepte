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
            username = session.username
            state = .signedIn
            await drainSharedImports()
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
        try KeychainStore.save(response.token, account: tokenAccount)
        try saveCloudflareCredentials(cloudflareCredentials)
        defaults.set(server.trimmingCharacters(in: .whitespacesAndNewlines), forKey: serverKey)
        try await api.configure(
            server: server,
            token: response.token,
            cloudflareCredentials: cloudflareCredentials
        )
        self.username = response.username
        state = .signedIn
        await drainSharedImports()
    }

    func signOut() {
        KeychainStore.delete(account: tokenAccount)
        username = ""
        state = .signedOut
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

    func handle(_ error: Error) {
        if let apiError = error as? APIError,
           case .unauthenticated = apiError {
            signOut()
        }
        alertMessage = error.localizedDescription
    }

    func drainSharedImports() async {
        guard case .signedIn = state else { return }
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
                ? "Der geteilte TikTok-/Instagram-Link wurde importiert."
                : "\(imported) geteilte Links wurden importiert."
        }
    }
}
