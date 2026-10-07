import XCTest
@testable import Rezepte

final class LegacyAccessMigrationTests: XCTestCase {
    func testUpgradeRemovesDeviceCredentialsAndPreservesRealSessions() {
        for token in ["account-session-token", "guest-session-token"] {
            var credentials = [
                "api-token": token,
                "cloudflare-client-id": "legacy-device-id",
                "cloudflare-client-secret": "legacy-device-secret",
                "unrelated-account": "keep-this-value",
            ]

            LegacyAccessMigration.removeCredentials(
                read: { credentials[$0] },
                delete: { credentials.removeValue(forKey: $0); return true }
            )

            XCTAssertEqual(credentials, ["api-token": token, "unrelated-account": "keep-this-value"])
        }
    }

    func testUpgradeDiscardsLegacyPseudoTokenAndIsIdempotent() {
        var credentials = [
            "api-token": "cloudflare-access",
            "cloudflare-client-id": "legacy-device-id",
            "cloudflare-client-secret": "legacy-device-secret",
        ]

        for _ in 0..<2 {
            LegacyAccessMigration.removeCredentials(
                read: { credentials[$0] },
                delete: { credentials.removeValue(forKey: $0); return true }
            )
            XCTAssertTrue(credentials.isEmpty)
        }
    }

    func testUpgradeAlsoCleansDeviceCredentialsWithoutAnySession() {
        var credentials = ["cloudflare-client-secret": "legacy-device-secret"]

        LegacyAccessMigration.removeCredentials(
            read: { credentials[$0] },
            delete: { credentials.removeValue(forKey: $0); return true }
        )

        XCTAssertTrue(credentials.isEmpty)
    }

    func testLegacyTokenRecognitionDoesNotMatchRealSessionValues() {
        XCTAssertTrue(LegacyAccessMigration.isLegacyToken("  cloudflare-access\n"))
        XCTAssertFalse(LegacyAccessMigration.isLegacyToken("cloudflare-access-real-token"))
        XCTAssertFalse(LegacyAccessMigration.isLegacyToken(nil))
    }

    func testFailedCleanupRetriesWithoutRemovingRealSession() {
        var credentials = [
            "api-token": "real-account-session",
            "cloudflare-client-id": "legacy-id",
            "cloudflare-client-secret": "legacy-secret",
        ]
        let completed = LegacyAccessMigration.removeCredentials(
            read: { credentials[$0] },
            delete: { key in
                if key == "cloudflare-client-secret" { return false }
                credentials.removeValue(forKey: key)
                return true
            }
        )
        XCTAssertFalse(completed)
        XCTAssertEqual(credentials["api-token"], "real-account-session")
        XCTAssertEqual(credentials["cloudflare-client-secret"], "legacy-secret")

        XCTAssertTrue(LegacyAccessMigration.removeCredentials(
            read: { credentials[$0] },
            delete: { credentials.removeValue(forKey: $0); return true }
        ))
        XCTAssertEqual(credentials, ["api-token": "real-account-session"])
    }
}
