import Foundation
import XCTest
@testable import Rezepte

final class LinkImportConsentStoreTests: XCTestCase {
    private var suite = ""
    private var defaults: UserDefaults!

    override func setUpWithError() throws {
        suite = "link-import-consent-tests-\(UUID().uuidString)"
        defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
        defaults = nil
    }

    func testGrantRestoresOnlyForSameNormalizedServerAndAccount() throws {
        let store = LinkImportConsentStore(defaults: defaults)
        XCTAssertNil(store.grant(server: "https://example.de", userID: 1))
        let grant = store.approve(server: " https://example.de/ ", userID: 1)
        let restored = LinkImportConsentStore(defaults: defaults)
        XCTAssertEqual(restored.grant(server: "https://example.de", userID: 1), grant)
        XCTAssertEqual(grant.server, "https://example.de")
        XCTAssertEqual(grant.version, AIProcessingConsent.version)
        XCTAssertNil(restored.grant(server: "https://other.example", userID: 1))
        XCTAssertNil(restored.grant(server: "https://example.de", userID: 2))
        XCTAssertNil(restored.grant(server: "https://example.de/path", userID: 1))
    }

    func testReapprovalRotatesScopeAndRevokePersists() {
        let store = LinkImportConsentStore(defaults: defaults)
        let first = store.approve(server: "https://example.de", userID: 1)
        let second = store.approve(server: "https://example.de", userID: 2)
        XCTAssertNotEqual(first.id, second.id)
        XCTAssertNil(store.grant(server: "https://example.de", userID: 1))
        XCTAssertEqual(store.grant(server: "https://example.de", userID: 2), second)
        store.revoke()
        XCTAssertNil(LinkImportConsentStore(defaults: defaults).grant(server: "https://example.de", userID: 2))
    }

    func testUnknownConsentVersionAndCorruptDocumentNeverAuthorize() throws {
        let stale = LinkImportGrant(id: UUID(), server: "https://example.de", userID: 1, version: "old-disclosure")
        defaults.set(try JSONEncoder().encode(stale), forKey: "automatic-link-import-consent-v1")
        let store = LinkImportConsentStore(defaults: defaults)
        XCTAssertNil(store.grant(server: "https://example.de", userID: 1))
        defaults.set(Data("invalid".utf8), forKey: "automatic-link-import-consent-v1")
        XCTAssertNil(store.grant(server: "https://example.de", userID: 1))
    }

    func testInvalidOrCredentialBearingServerAndMissingUserCannotGrant() {
        let store = LinkImportConsentStore(defaults: defaults)
        for server in ["not a server", "file:///tmp/recipe", "https://user:secret@example.de"] {
            store.approve(server: server, userID: 1)
            XCTAssertNil(store.grant(server: server, userID: 1))
            XCTAssertNil(defaults.data(forKey: "automatic-link-import-consent-v1"))
        }
        store.approve(server: "https://example.de", userID: 0)
        XCTAssertNil(store.grant(server: "https://example.de", userID: 0))
    }
}
