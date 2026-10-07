import Foundation

enum LegacyAccessMigration {
    static func isLegacyToken(_ token: String?) -> Bool {
        token?.trimmingCharacters(in: .whitespacesAndNewlines) == "cloudflare-access"
    }

    /// Idempotent upgrade cleanup. Genuine account and guest sessions survive.
    @discardableResult
    static func removeCredentials(
        read: (String) -> String?,
        delete: (String) -> Bool
    ) -> Bool {
        let idRemoved = delete("cloudflare-client-id")
        let secretRemoved = delete("cloudflare-client-secret")
        var tokenRemoved = true
        if isLegacyToken(read("api-token")) {
            tokenRemoved = delete("api-token")
        }
        return idRemoved && secretRemoved && tokenRemoved
    }
}
