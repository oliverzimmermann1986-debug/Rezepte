import Foundation

struct LinkImportGrant: Codable, Equatable, Sendable {
    let id: UUID
    let server: String
    let userID: Int
    let version: String
}

/// One device-local permission for link imports. No session token or credentials are stored.
final class LinkImportConsentStore {
    private let defaults: UserDefaults
    private static let key = "automatic-link-import-consent-v1"

    init(defaults: UserDefaults = .standard) { self.defaults = defaults }

    func grant(server: String, userID: Int) -> LinkImportGrant? {
        guard userID > 0, let server = Self.normalizedServer(server),
              let data = defaults.data(forKey: Self.key),
              let grant = try? JSONDecoder().decode(LinkImportGrant.self, from: data),
              grant.server == server, grant.userID == userID,
              grant.version == AIProcessingConsent.version else { return nil }
        return grant
    }

    @discardableResult
    func approve(server: String, userID: Int) -> LinkImportGrant {
        let normalized = Self.normalizedServer(server)
        let grant = LinkImportGrant(id: UUID(), server: normalized ?? "", userID: userID,
                                    version: AIProcessingConsent.version)
        guard normalized != nil, userID > 0,
              let data = try? JSONEncoder().encode(grant) else {
            revoke()
            return grant
        }
        defaults.set(data, forKey: Self.key)
        return grant
    }

    func revoke() { defaults.removeObject(forKey: Self.key) }

    private static func normalizedServer(_ value: String) -> String? {
        guard let url = APIClient.normalizedServerURL(value),
              url.user == nil, url.password == nil,
              let host = url.host, !host.isEmpty else { return nil }
        return url.absoluteString
    }
}
