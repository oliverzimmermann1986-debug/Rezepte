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
    private let serverKey = "server-url"

    var savedServer: String {
        defaults.string(forKey: serverKey) ?? ""
    }

    func restore() async {
        guard !savedServer.isEmpty,
              let token = KeychainStore.read(account: tokenAccount) else {
            state = .signedOut
            return
        }
        do {
            try await api.configure(server: savedServer, token: token)
            let session = try await api.sessionInfo()
            username = session.username
            state = .signedIn
        } catch {
            signOut()
        }
    }

    func signIn(server: String, username: String, password: String) async throws {
        try await api.configure(server: server, token: nil)
        let response = try await api.login(username: username, password: password)
        try KeychainStore.save(response.token, account: tokenAccount)
        defaults.set(server.trimmingCharacters(in: .whitespacesAndNewlines), forKey: serverKey)
        try await api.configure(server: server, token: response.token)
        self.username = response.username
        state = .signedIn
    }

    func signOut() {
        KeychainStore.delete(account: tokenAccount)
        username = ""
        state = .signedOut
    }

    func handle(_ error: Error) {
        if let apiError = error as? APIError,
           case .unauthenticated = apiError {
            signOut()
        }
        alertMessage = error.localizedDescription
    }
}
