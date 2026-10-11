import Foundation
import XCTest
@testable import Rezepte

final class AutomaticLinkImportTests: XCTestCase {
    @MainActor
    func testRememberingConsentProcessesTheExistingQueueAndSendsVersionedApproval() async throws {
        let fixture = try AutomaticLinkImportFixture()
        defer { fixture.cleanUp() }
        fixture.queue.enqueue("https://source.example/first")
        fixture.queue.enqueue("https://source.example/second")
        await fixture.restore()
        let before = MockURLProtocol.requestCount

        XCTAssertNil(fixture.queue.scope(for: "https://source.example/first"))
        XCTAssertTrue(fixture.store.rememberLinkImportConsent())
        XCTAssertNotNil(fixture.queue.scope(for: "https://source.example/first"))
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        await fixture.store.drainSharedImports()

        XCTAssertEqual(MockURLProtocol.requestCount, before + 2)
        XCTAssertTrue(fixture.queue.all().isEmpty)
        XCTAssertTrue(fixture.store.queuedSharedImports.isEmpty)
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: MockURLProtocol.lastBody()) as? [String: String])
        XCTAssertEqual(body["url"], "https://source.example/second")
        XCTAssertEqual(body["ai_processing_consent"], AIProcessingConsent.version)
        XCTAssertEqual(body["visibility"], "private")
    }

    @MainActor
    func testRememberedConsentReturnsFreshSingleUseTicketsWithoutAnotherPrompt() async throws {
        let fixture = try AutomaticLinkImportFixture()
        defer { fixture.cleanUp() }
        await fixture.restore()
        XCTAssertTrue(fixture.store.rememberLinkImportConsent())
        let coordinator = AIConsentCoordinator()

        let firstResult = await coordinator.request(.importLink, session: fixture.store)
        let secondResult = await coordinator.request(.importLink, session: fixture.store)
        let first = try XCTUnwrap(firstResult)
        let second = try XCTUnwrap(secondResult)
        XCTAssertNotEqual(first.id, second.id)
        XCTAssertNil(coordinator.pending)
        XCTAssertEqual(first.identity, fixture.store.identity)
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        let before = MockURLProtocol.requestCount
        _ = try await fixture.store.api.importURL("https://source.example/first", consent: first)
        _ = try await fixture.store.api.importURL("https://source.example/second", consent: second)
        do {
            _ = try await fixture.store.api.importURL("https://source.example/replayed", consent: first)
            XCTFail("Remembered permission must not make an individual ticket reusable")
        } catch APIError.aiConsentRequired {}
        XCTAssertEqual(MockURLProtocol.requestCount, before + 2)
    }

    @MainActor
    func testPersistentApprovalButtonRemembersOnlyLinkImports() async throws {
        let fixture = try AutomaticLinkImportFixture()
        defer { fixture.cleanUp() }
        await fixture.restore()
        let coordinator = AIConsentCoordinator()
        let decision = Task { await coordinator.request(.importLink, session: fixture.store) }
        await waitForPrompt(coordinator)
        XCTAssertTrue(coordinator.pending?.canRememberLinkImports == true)
        coordinator.approve(session: fixture.store, rememberLinkImports: true)
        let consent = await decision.value

        XCTAssertNotNil(consent)
        XCTAssertTrue(fixture.store.automaticLinkImportsEnabled)
        XCTAssertNil(coordinator.pending)
    }

    @MainActor
    func testOneTimeApprovalDoesNotEnableAutomaticProcessing() async throws {
        let fixture = try AutomaticLinkImportFixture()
        defer { fixture.cleanUp() }
        await fixture.restore()
        let coordinator = AIConsentCoordinator()
        let decision = Task { await coordinator.request(.importLink, session: fixture.store) }
        await waitForPrompt(coordinator)
        coordinator.approve(session: fixture.store, rememberLinkImports: false)
        let consent = await decision.value

        XCTAssertNotNil(consent)
        XCTAssertFalse(fixture.store.automaticLinkImportsEnabled)
        fixture.queue.enqueue("https://source.example/later")
        let before = MockURLProtocol.requestCount
        await fixture.store.drainSharedImports()
        XCTAssertEqual(MockURLProtocol.requestCount, before)
        XCTAssertEqual(fixture.queue.all(), ["https://source.example/later"])
    }

    @MainActor
    func testPhotoAndPortionActionsStillRequireTheirOwnConsent() async throws {
        let fixture = try AutomaticLinkImportFixture()
        defer { fixture.cleanUp() }
        await fixture.restore()
        XCTAssertTrue(fixture.store.rememberLinkImportConsent())
        let coordinator = AIConsentCoordinator()
        let before = MockURLProtocol.requestCount

        for action in [AIProcessingAction.importFile, .servingsEstimate] {
            let decision = Task { await coordinator.request(action, session: fixture.store) }
            await waitForPrompt(coordinator)
            XCTAssertEqual(coordinator.pending?.action, action)
            XCTAssertFalse(coordinator.pending?.canRememberLinkImports ?? true)
            coordinator.cancel()
            let consent = await decision.value
            XCTAssertNil(consent)
        }
        XCTAssertEqual(MockURLProtocol.requestCount, before)
        XCTAssertTrue(fixture.store.automaticLinkImportsEnabled)
    }

    @MainActor
    func testNormalAccountCanStillApproveAnAllowedNonImportAction() async throws {
        let fixture = try AutomaticLinkImportFixture()
        defer { fixture.cleanUp() }
        MockURLProtocol.respond(json: fixture.response(role: "user"))
        await fixture.store.restore()
        XCTAssertFalse(fixture.store.canImport)
        let coordinator = AIConsentCoordinator()
        let before = MockURLProtocol.requestCount
        let decision = Task { await coordinator.request(.servingsEstimate, session: fixture.store) }
        await waitForPrompt(coordinator)
        XCTAssertFalse(coordinator.pending?.canRememberLinkImports ?? true)

        coordinator.approve(session: fixture.store, rememberLinkImports: false)
        let result = await decision.value
        let consent = try XCTUnwrap(result)

        XCTAssertEqual(consent.action, .servingsEstimate)
        XCTAssertEqual(consent.identity, fixture.store.identity)
        XCTAssertFalse(fixture.store.automaticLinkImportsEnabled)
        XCTAssertEqual(MockURLProtocol.requestCount, before)
    }

    @MainActor
    func testRelaunchForTheSameAccountAutomaticallyProcessesANewSharedLink() async throws {
        let fixture = try AutomaticLinkImportFixture()
        defer { fixture.cleanUp() }
        await fixture.restore()
        XCTAssertTrue(fixture.store.rememberLinkImportConsent())
        let previousIdentity = fixture.store.identity
        fixture.queue.enqueue("https://source.example/while-app-closed")
        let relaunched = fixture.makeStore()
        let before = MockURLProtocol.requestCount
        MockURLProtocol.respond(json: fixture.response())

        await relaunched.restore()

        XCTAssertNotEqual(relaunched.identity, previousIdentity)
        XCTAssertTrue(relaunched.automaticLinkImportsEnabled)
        XCTAssertEqual(MockURLProtocol.requestCount, before + 3, "Verify session and server, then import once")
        XCTAssertEqual(MockURLProtocol.lastPath(), "/api/pending/import-url")
        XCTAssertTrue(fixture.queue.all().isEmpty)
    }

    @MainActor
    func testDifferentAccountCannotAutomaticallyClaimThePreviousAccountsQueue() async throws {
        let fixture = try AutomaticLinkImportFixture()
        defer { fixture.cleanUp() }
        await fixture.restore()
        XCTAssertTrue(fixture.store.rememberLinkImportConsent())
        let oldURL = "https://source.example/previous-account"
        fixture.queue.enqueue(oldURL)
        let oldScope = try XCTUnwrap(fixture.queue.scope(for: oldURL))
        let otherAccount = fixture.makeStore()
        MockURLProtocol.respond(json: fixture.response(userID: 99))
        let before = MockURLProtocol.requestCount
        await otherAccount.restore()

        XCTAssertFalse(otherAccount.automaticLinkImportsEnabled)
        XCTAssertNil(otherAccount.automaticLinkImportConsent())
        XCTAssertEqual(MockURLProtocol.requestCount, before + 2)
        XCTAssertTrue(otherAccount.rememberLinkImportConsent())
        let newURL = "https://source.example/current-account"
        fixture.queue.enqueue(newURL)
        XCTAssertNotEqual(fixture.queue.scope(for: newURL), oldScope)
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        await otherAccount.drainSharedImports()

        XCTAssertEqual(MockURLProtocol.requestCount, before + 3)
        XCTAssertEqual(fixture.queue.all(), [oldURL])
        XCTAssertEqual(fixture.queue.scope(for: oldURL), oldScope)
    }

    @MainActor
    func testDifferentServerDoesNotInheritTheSavedPermission() async throws {
        let fixture = try AutomaticLinkImportFixture()
        defer { fixture.cleanUp() }
        await fixture.restore()
        XCTAssertTrue(fixture.store.rememberLinkImportConsent())
        fixture.queue.enqueue("https://source.example/old-server")
        fixture.defaults.set("https://other.example", forKey: "server-url")
        let otherServer = fixture.makeStore()
        MockURLProtocol.respond(json: fixture.response())
        let before = MockURLProtocol.requestCount

        await otherServer.restore()

        XCTAssertFalse(otherServer.automaticLinkImportsEnabled)
        XCTAssertNil(otherServer.automaticLinkImportConsent())
        XCTAssertEqual(MockURLProtocol.requestCount, before + 2)
        XCTAssertEqual(fixture.queue.all(), ["https://source.example/old-server"])
    }

    @MainActor
    func testRevokingDuringAnImportStopsBeforeTheNextLink() async throws {
        let fixture = try AutomaticLinkImportFixture()
        defer { fixture.cleanUp() }
        await fixture.restore()
        XCTAssertTrue(fixture.store.rememberLinkImportConsent())
        fixture.queue.enqueue("https://source.example/first")
        fixture.queue.enqueue("https://source.example/second")
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        let started = expectation(description: "The first import reached the server")
        MockURLProtocol.suspendNextResponse { started.fulfill() }
        let before = MockURLProtocol.requestCount
        let importing = Task { await fixture.store.drainSharedImports() }
        await fulfillment(of: [started], timeout: 2)
        fixture.store.revokeLinkImportConsent()
        MockURLProtocol.resumeResponse()
        await importing.value
        await fixture.store.drainSharedImports()

        XCTAssertFalse(fixture.store.automaticLinkImportsEnabled)
        XCTAssertNil(fixture.store.automaticLinkImportConsent())
        XCTAssertEqual(MockURLProtocol.requestCount, before + 1)
        XCTAssertEqual(fixture.queue.all(), ["https://source.example/second"])
        XCTAssertFalse(fixture.queue.wasAttempted("https://source.example/second"))
    }

    @MainActor
    func testConcurrentForegroundDrainsSendEachSharedLinkOnlyOnce() async throws {
        let fixture = try AutomaticLinkImportFixture()
        defer { fixture.cleanUp() }
        await fixture.restore()
        XCTAssertTrue(fixture.store.rememberLinkImportConsent())
        fixture.queue.enqueue("https://source.example/first")
        fixture.queue.enqueue("https://source.example/second")
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        let started = expectation(description: "The first drain is suspended")
        MockURLProtocol.suspendNextResponse { started.fulfill() }
        let before = MockURLProtocol.requestCount
        let importing = Task { await fixture.store.drainSharedImports() }
        await fulfillment(of: [started], timeout: 2)
        await fixture.store.drainSharedImports()
        XCTAssertEqual(MockURLProtocol.requestCount, before + 1)
        MockURLProtocol.resumeResponse()
        await importing.value

        XCTAssertEqual(MockURLProtocol.requestCount, before + 2)
        XCTAssertTrue(fixture.queue.all().isEmpty)
    }

    @MainActor
    func testFailedImportStaysQueuedWithoutAnAutomaticRetryAfterRelaunch() async throws {
        let fixture = try AutomaticLinkImportFixture()
        defer { fixture.cleanUp() }
        await fixture.restore()
        XCTAssertTrue(fixture.store.rememberLinkImportConsent())
        let url = "https://source.example/uncertain-result"
        fixture.queue.enqueue(url)
        MockURLProtocol.respond(body: #"{"detail":"Server temporarily unavailable"}"#, statusCode: 503)
        let before = MockURLProtocol.requestCount
        await fixture.store.drainSharedImports()
        await fixture.store.drainSharedImports()
        XCTAssertEqual(MockURLProtocol.requestCount, before + 1)
        XCTAssertEqual(fixture.queue.all(), [url])
        XCTAssertTrue(fixture.queue.wasAttempted(url))

        let relaunched = fixture.makeStore()
        MockURLProtocol.respond(json: fixture.response())
        await relaunched.restore()
        XCTAssertEqual(MockURLProtocol.requestCount, before + 3, "Relaunch verifies the session but never repeats the failed import")
        XCTAssertEqual(fixture.queue.all(), [url])
        let consent = try XCTUnwrap(relaunched.automaticLinkImportConsent())
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        await relaunched.importSharedURL(url, consent: consent)
        XCTAssertEqual(MockURLProtocol.requestCount, before + 4, "An explicit manual retry remains available")
        XCTAssertTrue(fixture.queue.all().isEmpty)
    }

    @MainActor
    func testUnsuccessfulResultDoesNotRemoveTheLinkOrContinueTheBatch() async throws {
        let fixture = try AutomaticLinkImportFixture()
        defer { fixture.cleanUp() }
        await fixture.restore()
        XCTAssertTrue(fixture.store.rememberLinkImportConsent())
        let first = "https://source.example/first"
        let second = "https://source.example/second"
        fixture.queue.enqueue(first)
        fixture.queue.enqueue(second)
        MockURLProtocol.respond(json: #"{"ok":false,"message":"Import rejected"}"#)
        let before = MockURLProtocol.requestCount

        await fixture.store.drainSharedImports()

        XCTAssertEqual(MockURLProtocol.requestCount, before + 1)
        XCTAssertEqual(fixture.queue.all(), [first, second])
        XCTAssertTrue(fixture.queue.wasAttempted(first))
        XCTAssertFalse(fixture.queue.wasAttempted(second))
        XCTAssertEqual(fixture.store.alertMessage, "Import rejected")
    }

    @MainActor
    func testFailedAttemptPersistencePreventsAnOtherwiseApprovedImport() async throws {
        let fixture = try AutomaticLinkImportFixture()
        defer { fixture.cleanUp() }
        let parentFile = FileManager.default.temporaryDirectory.appendingPathComponent("ImportQueueParent-\(UUID().uuidString)")
        try Data("A file cannot contain a queue document".utf8).write(to: parentFile)
        defer { try? FileManager.default.removeItem(at: parentFile) }
        let failingQueue = SharedImportQueueStore(
            defaults: fixture.defaults, storageURL: parentFile.appendingPathComponent("queue.json")
        )
        let url = "https://source.example/must-not-transfer"
        var removed = false
        let persistence = LocalSessionPersistence(defaults: fixture.defaults, read: { "synthetic-token" },
                                                  save: { _ in }, delete: { true })
        let store = SessionStore(
            api: APIClient(session: MockURLProtocol.makeSession()), defaults: fixture.defaults,
            persistence: persistence, sharedImportURLs: { [url] },
            removeSharedImport: { _ in removed = true }, sharedImportQueue: failingQueue
        )
        MockURLProtocol.respond(json: fixture.response())
        await store.restore()
        let consent = AIProcessingConsent(id: UUID(), action: .importLink,
                                          identity: store.identity, server: "https://example.de")
        MockURLProtocol.respond(json: #"{"ok":true}"#)
        let before = MockURLProtocol.requestCount

        await store.importSharedURL(url, consent: consent)

        XCTAssertEqual(MockURLProtocol.requestCount, before, "Do not transfer if retry protection cannot be persisted")
        XCTAssertFalse(removed)
        XCTAssertEqual(store.queuedSharedImports, [url])
        XCTAssertFalse(failingQueue.wasAttempted(url))
    }

    @MainActor
    func testUnprivilegedAccountsAndUnsupportedServersCannotEnableAutomaticImports() async throws {
        for (role, capability, userID) in [("user", true, 12), ("guest", true, 12), ("full_user", false, 12), ("admin", true, 0)] {
            let fixture = try AutomaticLinkImportFixture()
            defer { fixture.cleanUp() }
            fixture.queue.enqueue("https://source.example/untouched")
            MockURLProtocol.respond(json: fixture.response(role: role, userID: userID, capability: capability))
            await fixture.store.restore()
            let before = MockURLProtocol.requestCount

            XCTAssertFalse(fixture.store.canEnableAutomaticLinkImports)
            XCTAssertFalse(fixture.store.rememberLinkImportConsent())
            XCTAssertNil(fixture.store.automaticLinkImportConsent())
            await fixture.store.drainSharedImports()

            XCTAssertEqual(MockURLProtocol.requestCount, before)
            XCTAssertEqual(fixture.queue.all(), ["https://source.example/untouched"])
        }
    }

    @MainActor
    func testOfflineRestorationKeepsPermissionButDoesNotDispatchImports() async throws {
        let fixture = try AutomaticLinkImportFixture()
        defer { fixture.cleanUp() }
        await fixture.restore()
        XCTAssertTrue(fixture.store.rememberLinkImportConsent())
        fixture.queue.enqueue("https://source.example/wait-for-network")
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [AutomaticLinkImportOfflineProtocol.self]
        let offline = fixture.makeStore(session: URLSession(configuration: configuration))
        let before = AutomaticLinkImportOfflineProtocol.requestCount
        await offline.restore()

        XCTAssertTrue(offline.isOfflineSession)
        XCTAssertTrue(offline.automaticLinkImportsEnabled)
        XCTAssertFalse(offline.canEnableAutomaticLinkImports)
        XCTAssertNil(offline.automaticLinkImportConsent())
        await offline.drainSharedImports()

        XCTAssertEqual(AutomaticLinkImportOfflineProtocol.requestCount, before + 1, "Only session restoration attempted a network request")
        XCTAssertEqual(fixture.queue.all(), ["https://source.example/wait-for-network"])
        XCTAssertFalse(fixture.queue.wasAttempted("https://source.example/wait-for-network"))
    }

    @MainActor
    func testFailedAccessRefreshStopsAutomaticProcessingUntilTheAccountIsVerifiedAgain() async throws {
        let fixture = try AutomaticLinkImportFixture()
        defer { fixture.cleanUp() }
        await fixture.restore()
        XCTAssertTrue(fixture.store.rememberLinkImportConsent())
        fixture.queue.enqueue("https://source.example/wait-for-session")
        MockURLProtocol.respond(body: #"{"detail":"Session verification unavailable"}"#, statusCode: 503)
        await fixture.store.refreshAccess()
        let before = MockURLProtocol.requestCount
        await fixture.store.drainSharedImports()

        XCTAssertFalse(fixture.store.canEnableAutomaticLinkImports)
        XCTAssertNil(fixture.store.automaticLinkImportConsent())
        XCTAssertEqual(MockURLProtocol.requestCount, before)
        XCTAssertEqual(fixture.queue.all(), ["https://source.example/wait-for-session"])

        MockURLProtocol.respond(json: fixture.response())
        await fixture.store.refreshAccess()
        await fixture.store.drainSharedImports()
        XCTAssertEqual(MockURLProtocol.requestCount, before + 2)
        XCTAssertTrue(fixture.queue.all().isEmpty)
    }

    @MainActor
    func testSignOutRemovesPermissionAndNeverTransfersTheRemainingQueue() async throws {
        let fixture = try AutomaticLinkImportFixture()
        defer { fixture.cleanUp() }
        await fixture.restore()
        XCTAssertTrue(fixture.store.rememberLinkImportConsent())
        fixture.queue.enqueue("https://source.example/old-session")
        let before = MockURLProtocol.requestCount

        fixture.store.signOut()
        await fixture.store.drainSharedImports()

        XCTAssertFalse(fixture.store.automaticLinkImportsEnabled)
        XCTAssertNil(fixture.store.automaticLinkImportConsent())
        XCTAssertEqual(MockURLProtocol.requestCount, before)
        XCTAssertEqual(fixture.queue.all(), ["https://source.example/old-session"])
        fixture.queue.enqueue("https://source.example/signed-out")
        XCTAssertNil(fixture.queue.scope(for: "https://source.example/signed-out"))
    }

    @MainActor
    func testHouseholdChangeRevokesAutomaticProcessingBeforeRevalidatingTheSession() async throws {
        let fixture = try AutomaticLinkImportFixture()
        defer { fixture.cleanUp() }
        await fixture.restore()
        XCTAssertTrue(fixture.store.rememberLinkImportConsent())
        fixture.queue.enqueue("https://source.example/previous-household")
        MockURLProtocol.respond(json: fixture.response())
        let before = MockURLProtocol.requestCount

        try await fixture.store.householdDidChange()
        await fixture.store.drainSharedImports()

        XCTAssertFalse(fixture.store.automaticLinkImportsEnabled)
        XCTAssertNil(fixture.store.automaticLinkImportConsent())
        XCTAssertEqual(MockURLProtocol.requestCount, before + 1)
        XCTAssertEqual(fixture.queue.all(), ["https://source.example/previous-household"])
    }

    @MainActor
    private func waitForPrompt(_ coordinator: AIConsentCoordinator) async {
        for _ in 0..<1_000 {
            if coordinator.pending != nil { return }
            await Task.yield()
        }
        XCTFail("The consent prompt was not presented")
        coordinator.cancel()
    }
}

@MainActor
private final class AutomaticLinkImportFixture {
    let suite = "AutomaticLinkImportTests.\(UUID().uuidString)"
    let defaults: UserDefaults
    let queue: SharedImportQueueStore
    private var token: String? = "synthetic-link-import-token"
    lazy var store: SessionStore = makeStore()

    init() throws {
        defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defaults.set("https://example.de", forKey: "server-url")
        queue = SharedImportQueueStore(defaults: defaults)
    }

    func makeStore(session: URLSession = MockURLProtocol.makeSession()) -> SessionStore {
        let persistence = LocalSessionPersistence(
            defaults: defaults,
            read: { [weak self] in self?.token },
            save: { [weak self] in self?.token = $0 },
            delete: { [weak self] in self?.token = nil; return true }
        )
        return SessionStore(api: APIClient(session: session), defaults: defaults,
                            persistence: persistence, sharedImportQueue: queue)
    }

    func restore() async {
        MockURLProtocol.respond(json: response())
        await store.restore()
    }

    func response(role: String = "full_user", userID: Int = 12, capability: Bool = true) -> String {
        let consentCapability = capability ? "ai-action-consent-v1" : "legacy-feature"
        return #"{"id":\#(userID),"username":"link-test-account","role":"\#(role)","full_access":false,"read_only":false,"name":"Rezepte","version":"1.12.0","capabilities":["\#(consentCapability)","shopping-categories","recurring-shopping","weekly-meal-plan"],"ok":true}"#
    }

    func cleanUp() {
        MockURLProtocol.resumeResponse()
        defaults.removePersistentDomain(forName: suite)
    }
}

private final class AutomaticLinkImportOfflineProtocol: URLProtocol {
    nonisolated(unsafe) private(set) static var requestCount = 0
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.requestCount += 1
        client?.urlProtocol(self, didFailWithError: URLError(.notConnectedToInternet))
    }
    override func stopLoading() {}
}
