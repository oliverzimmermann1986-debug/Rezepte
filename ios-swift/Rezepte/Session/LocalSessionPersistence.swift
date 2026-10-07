import Foundation

/// The durable sign-out marker prevents a failed Keychain deletion from restoring
/// a session on the next launch. Only a successfully saved new session clears it.
struct LocalSessionPersistence {
    private let defaults: UserDefaults
    private let readToken: () -> String?
    private let saveToken: (String) throws -> Void
    private let deleteToken: () -> Bool
    private let signedOutKey = "session-explicitly-signed-out-v1"

    init(defaults: UserDefaults = .standard,
         read: @escaping () -> String? = { KeychainStore.read(account: "api-token") },
         save: @escaping (String) throws -> Void = { try KeychainStore.save($0, account: "api-token") },
         delete: @escaping () -> Bool = { KeychainStore.delete(account: "api-token") }) {
        self.defaults = defaults
        readToken = read
        saveToken = save
        deleteToken = delete
    }

    func restoredToken() -> String? {
        guard !defaults.bool(forKey: signedOutKey) else {
            _ = deleteToken()
            return nil
        }
        return readToken()
    }

    func activate(_ token: String) throws {
        try saveToken(token)
        defaults.set(false, forKey: signedOutKey)
    }

    func signOut() {
        defaults.set(true, forKey: signedOutKey)
        _ = deleteToken()
    }
}
