import Foundation
import XCTest
@testable import Rezepte

final class NativeAuthTests: XCTestCase {
    func testPKCEKnownS256VectorAndGeneratedProofs() throws {
        let vector = NativeAuthProof(verifier: "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        XCTAssertEqual(vector.challenge, "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")
        let first = try NativeAuthProof.make()
        let second = try NativeAuthProof.make()
        XCTAssertEqual(first.verifier.count, 43)
        XCTAssertEqual(first.challenge.count, 43)
        XCTAssertNotEqual(first.verifier, second.verifier)
        XCTAssertNotNil(first.verifier.range(of: "^[A-Za-z0-9_-]{43}$", options: .regularExpression))
    }

    func testOnlyExactFlowAndCallbackShapeCanYieldAnExchangeCode() throws {
        let base = "de.mausbaeren.rezepte://auth/callback"
        XCTAssertEqual(try NativeAuthCallback.code(from: XCTUnwrap(URL(string: base + "?code=one-use&flow_id=flow-1")), expectedFlow: "flow-1"), "one-use")
        let invalid = [
            "other://auth/callback?code=x&flow_id=flow-1",
            "de.mausbaeren.rezepte://other/callback?code=x&flow_id=flow-1",
            "de.mausbaeren.rezepte://auth/other?code=x&flow_id=flow-1",
            "de.mausbaeren.rezepte://user@auth/callback?code=x&flow_id=flow-1",
            "de.mausbaeren.rezepte://auth:42/callback?code=x&flow_id=flow-1",
            base + "?code=x&flow_id=flow-1#fragment",
            base + "?code=x&flow_id=wrong",
            base + "?code=x",
            base + "?flow_id=flow-1",
            base + "?code=&flow_id=flow-1",
            base + "?code=x&code=y&flow_id=flow-1",
            base + "?code=x&flow_id=flow-1&flow_id=flow-1",
            base + "?code=x&error=cancelled&flow_id=flow-1",
        ]
        for value in invalid {
            XCTAssertThrowsError(try NativeAuthCallback.code(from: XCTUnwrap(URL(string: value)), expectedFlow: "flow-1"), value)
        }
    }

    func testCancellationCallbackStillRequiresTheMatchingFlow() throws {
        let matching = try XCTUnwrap(URL(string: "de.mausbaeren.rezepte://auth/callback?error=cancelled&flow_id=flow-1"))
        XCTAssertThrowsError(try NativeAuthCallback.code(from: matching, expectedFlow: "flow-1")) {
            XCTAssertTrue($0 is CancellationError)
        }
        XCTAssertThrowsError(try NativeAuthCallback.code(from: matching, expectedFlow: "different-flow")) {
            XCTAssertFalse($0 is CancellationError)
        }
    }

    func testBrowserAuthorizationOnlyAcceptsKnownProviderHosts() throws {
        XCTAssertEqual(try NativeAuthCallback.authorizationURL("https://accounts.google.com/o/oauth2/v2/auth?state=x").host, "accounts.google.com")
        XCTAssertEqual(try NativeAuthCallback.authorizationURL("https://appleid.apple.com:443/auth/authorize").host, "appleid.apple.com")
        for value in ["http://accounts.google.com/auth", "https://user:password@accounts.google.com/auth",
                      "https://accounts.google.com:8443/auth", "https://accounts.google.com/auth#fragment",
                      "https://accounts.google.com.evil.example/auth", "https://example.com/auth",
                      "https://evil.example@appleid.apple.com/auth", "javascript:alert(1)", "/auth"] {
            XCTAssertThrowsError(try NativeAuthCallback.authorizationURL(value))
        }
    }

    func testDurableSignOutSurvivesFailedKeychainDeletionAndRetriesOnRestore() throws {
        let suite = "NativeAuthTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        var token: String? = "still-valid-old-token"
        var deletions = 0
        let persistence = LocalSessionPersistence(defaults: defaults, read: { token }, save: { token = $0 }, delete: { deletions += 1; return false })
        XCTAssertEqual(persistence.restoredToken(), "still-valid-old-token")
        persistence.signOut()
        XCTAssertNil(persistence.restoredToken())
        let nextLaunch = LocalSessionPersistence(defaults: defaults, read: { token }, save: { token = $0 }, delete: { deletions += 1; return false })
        XCTAssertNil(nextLaunch.restoredToken())
        XCTAssertEqual(token, "still-valid-old-token")
        XCTAssertEqual(deletions, 3)
        try nextLaunch.activate("new-session")
        XCTAssertEqual(nextLaunch.restoredToken(), "new-session")
    }

    func testFailedTokenSaveCannotUndoPersistentSignOut() throws {
        let suite = "NativeAuthTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let persistence = LocalSessionPersistence(defaults: defaults, read: { "old-token" },
            save: { _ in throw NSError(domain: "KeychainTest", code: 1) }, delete: { false })
        persistence.signOut()
        XCTAssertThrowsError(try persistence.activate("new-token"))
        XCTAssertNil(persistence.restoredToken())
    }

    @MainActor
    func testBrowserCancellationDoesNotExchangeOrPersistCredentials() async throws {
        let suite = "NativeAuthTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        var savedToken: String?
        let persistence = LocalSessionPersistence(defaults: defaults, read: { savedToken }, save: { savedToken = $0 }, delete: { savedToken = nil; return true })
        let browser = TestNativeAuthentication()
        browser.handler = { _ in throw CancellationError() }
        let api = APIClient(session: MockURLProtocol.makeSession())
        let store = SessionStore(api: api, defaults: defaults, persistence: persistence, webAuthentication: browser)
        MockURLProtocol.respond(json: #"{"authorization_url":"https://accounts.google.com/auth","flow_id":"flow-1"}"#)
        do {
            try await store.signInWithProvider(.google, server: "https://example.de")
            XCTFail("Expected cancellation")
        } catch { XCTAssertTrue(error is CancellationError) }
        XCTAssertNil(savedToken)
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/auth/google/start")
    }

    @MainActor
    func testLateProviderCallbackCannotExchangeAfterSignOut() async throws {
        let suite = "NativeAuthTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let persistence = LocalSessionPersistence(defaults: defaults, read: { nil }, save: { _ in XCTFail("Must not save a stale token") }, delete: { true })
        let browser = TestNativeAuthentication()
        let started = expectation(description: "Browser started")
        var pending: CheckedContinuation<URL, Error>?
        browser.handler = { _ in
            try await withCheckedThrowingContinuation { continuation in
                pending = continuation
                started.fulfill()
            }
        }
        let api = APIClient(session: MockURLProtocol.makeSession())
        let store = SessionStore(api: api, defaults: defaults, persistence: persistence, webAuthentication: browser)
        MockURLProtocol.respond(json: #"{"authorization_url":"https://appleid.apple.com/auth/authorize","flow_id":"flow-1"}"#)
        let login = Task { try await store.signInWithProvider(.apple, server: "https://example.de") }
        await fulfillment(of: [started], timeout: 2)
        store.signOut()
        pending?.resume(returning: try XCTUnwrap(URL(string: "de.mausbaeren.rezepte://auth/callback?code=late&flow_id=flow-1")))
        do {
            try await login.value
            XCTFail("Expected stale-session rejection")
        } catch let error as APIError {
            guard case .sessionChanged = error else { return XCTFail("Unexpected error: \(error)") }
        }
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/auth/apple/start")
        XCTAssertNil(persistence.restoredToken())
    }
}

@MainActor
private final class TestNativeAuthentication: NativeAuthenticating {
    var handler: ((URL) async throws -> URL)?
    func authenticate(url: URL) async throws -> URL {
        guard let handler else { throw CancellationError() }
        return try await handler(url)
    }
    // Intentionally permits a late callback to exercise the SessionStore guard.
    func cancel() {}
}
