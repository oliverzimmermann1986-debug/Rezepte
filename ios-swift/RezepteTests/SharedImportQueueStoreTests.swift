import Foundation
import XCTest
@testable import Rezepte

final class SharedImportQueueStoreTests: XCTestCase {
    private var suite = ""
    private var defaults: UserDefaults!
    private var temporaryDirectories: [URL] = []

    override func setUpWithError() throws {
        suite = "shared-import-queue-tests-\(UUID().uuidString)"
        defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
    }

    override func tearDown() {
        for directory in temporaryDirectories { try? FileManager.default.removeItem(at: directory) }
        temporaryDirectories = []
        defaults.removePersistentDomain(forName: suite)
        defaults = nil
    }

    func testLegacyLinksStayUnboundUntilExplicitClaim() {
        let oldLink = "https://example.de/legacy"
        defaults.set([oldLink], forKey: "shared-import-urls")
        let store = SharedImportQueueStore(defaults: defaults)
        XCTAssertEqual(store.all(), [oldLink])
        XCTAssertNil(store.scope(for: oldLink))
        store.setActiveScope("account-a")
        XCTAssertNil(store.scope(for: oldLink), "Activating an account must not adopt old links")
        store.claimUnassigned(scope: "account-a")
        XCTAssertEqual(store.scope(for: oldLink), "account-a")
        XCTAssertEqual(SharedImportQueueStore(defaults: defaults).scope(for: oldLink), "account-a")
    }

    func testAccountSwitchAndClaimNeverReassignPreviouslyBoundLinks() {
        let store = SharedImportQueueStore(defaults: defaults)
        store.setActiveScope("account-a")
        store.enqueue("https://example.de/a")
        store.setActiveScope(nil)
        store.enqueue("https://example.de/unassigned")
        store.setActiveScope("account-b")
        store.enqueue("https://example.de/b")
        store.claimUnassigned(scope: "account-b")
        XCTAssertEqual(store.scope(for: "https://example.de/a"), "account-a")
        XCTAssertEqual(store.scope(for: "https://example.de/b"), "account-b")
        XCTAssertEqual(store.scope(for: "https://example.de/unassigned"), "account-b")
        store.enqueue("https://example.de/a")
        XCTAssertEqual(store.all().count, 3)
        XCTAssertEqual(store.scope(for: "https://example.de/a"), "account-a")
    }

    func testAttemptPersistsAndRemovalClearsScopeAndAttemptBeforeReenqueue() {
        let link = "https://example.de/recipe"
        let store = SharedImportQueueStore(defaults: defaults)
        store.setActiveScope("account-a")
        store.enqueue(link)
        XCTAssertFalse(store.wasAttempted(link))
        store.markAttempted(link)
        store.markAttempted(link)
        let restored = SharedImportQueueStore(defaults: defaults)
        XCTAssertTrue(restored.wasAttempted(link))
        restored.remove(link)
        XCTAssertNil(restored.scope(for: link))
        XCTAssertFalse(restored.wasAttempted(link))
        restored.setActiveScope("account-b")
        restored.enqueue(link)
        XCTAssertEqual(restored.scope(for: link), "account-b")
        XCTAssertFalse(restored.wasAttempted(link))
    }

    func testClearedScopeDoesNotLeakIntoNewLinksAndUnknownAttemptsAreIgnored() {
        let store = SharedImportQueueStore(defaults: defaults)
        store.setActiveScope("account-a")
        store.setActiveScope("")
        let link = "https://example.de/new"
        store.markAttempted(link)
        store.enqueue(link)
        XCTAssertNil(store.scope(for: link))
        XCTAssertFalse(store.wasAttempted(link))
        store.claimUnassigned(scope: "")
        XCTAssertNil(store.scope(for: link))
    }

    private func queueFile() throws -> URL {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("shared-queue-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        temporaryDirectories.append(directory)
        return directory.appendingPathComponent("queue.json")
    }

    func testCoordinatedFileMigratesLegacyOnceAndPersistsAcrossInstances() throws {
        let file = try queueFile()
        defaults.set(["https://example.de/legacy"], forKey: "shared-import-urls")
        defaults.set("old-account", forKey: "shared-import-active-scope-v1")
        defaults.set(["https://example.de/legacy": "old-account"], forKey: "shared-import-url-scopes-v1")
        let app = SharedImportQueueStore(defaults: defaults, storageURL: file)
        XCTAssertEqual(app.all(), ["https://example.de/legacy"])
        XCTAssertNil(app.scope(for: "https://example.de/legacy"))
        XCTAssertNil(defaults.object(forKey: "shared-import-urls"))
        app.claimUnassigned(scope: "confirmed-account")
        app.setActiveScope("confirmed-account")
        let share = SharedImportQueueStore(defaults: defaults, storageURL: file)
        XCTAssertTrue(share.enqueue("https://example.de/new"))
        XCTAssertEqual(app.scope(for: "https://example.de/new"), "confirmed-account")
        XCTAssertTrue(app.markAttempted("https://example.de/new"))
        XCTAssertTrue(share.wasAttempted("https://example.de/new"))
        app.remove("https://example.de/new")
        XCTAssertFalse(share.all().contains("https://example.de/new"))
        defaults.set(["https://example.de/must-not-return"], forKey: "shared-import-urls")
        XCTAssertEqual(share.all(), ["https://example.de/legacy"], "A valid queue never reads stale legacy state again")
    }

    func testConcurrentInstancesDoNotLoseEnqueuesOrTheirScope() throws {
        let file = try queueFile()
        let seed = SharedImportQueueStore(defaults: defaults, storageURL: file)
        seed.setActiveScope("confirmed-account")
        let testDefaults = try XCTUnwrap(defaults)
        DispatchQueue.concurrentPerform(iterations: 24) { index in
            let writer = SharedImportQueueStore(defaults: testDefaults, storageURL: file)
            _ = writer.enqueue("https://example.de/recipe-\(index)")
        }
        XCTAssertEqual(Set(seed.all()).count, 24)
        for index in 0..<24 { XCTAssertEqual(seed.scope(for: "https://example.de/recipe-\(index)"), "confirmed-account") }
    }

    func testCorruptOrFutureFileCannotMigrateLegacyOrReportAttemptSuccess() throws {
        let file = try queueFile()
        let link = "https://example.de/legacy"
        defaults.set([link], forKey: "shared-import-urls")
        let store = SharedImportQueueStore(defaults: defaults, storageURL: file)
        for content in ["invalid JSON", #"{"version":99,"entries":[]}"#] {
            let data = Data(content.utf8)
            try data.write(to: file)
            XCTAssertTrue(store.all().isEmpty)
            XCTAssertFalse(store.markAttempted(link))
            XCTAssertFalse(store.enqueue("https://example.de/new"))
            store.remove(link)
            XCTAssertEqual(try Data(contentsOf: file), data)
            XCTAssertEqual(defaults.stringArray(forKey: "shared-import-urls"), [link])
        }
    }

    func testUnavailableFileWriteFailsClosedAndPreservesLegacy() throws {
        let file = try queueFile()
        try FileManager.default.createDirectory(at: file, withIntermediateDirectories: false)
        defaults.set(["https://example.de/legacy"], forKey: "shared-import-urls")
        let store = SharedImportQueueStore(defaults: defaults, storageURL: file)
        XCTAssertFalse(store.enqueue("https://example.de/new"))
        XCTAssertFalse(store.markAttempted("https://example.de/legacy"))
        XCTAssertEqual(defaults.stringArray(forKey: "shared-import-urls"), ["https://example.de/legacy"])
    }
}
