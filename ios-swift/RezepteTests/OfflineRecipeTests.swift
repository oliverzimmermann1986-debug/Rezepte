import Foundation
import UIKit
import XCTest
@testable import Rezepte

@MainActor
final class OfflineRecipeTests: XCTestCase {
    @MainActor
    private final class HouseholdGate {
        var continuation: CheckedContinuation<Int, Never>?
        func wait(started: XCTestExpectation) async -> Int {
            await withCheckedContinuation { continuation in
                self.continuation = continuation
                started.fulfill()
            }
        }
        func resume(_ household: Int) {
            continuation?.resume(returning: household)
            continuation = nil
        }
    }

    private final class Disk {
        var files: [String: Data] = [:]
        var failIndex = false
        var failErase = false
        var storage: OfflineRecipeStorage {
            OfflineRecipeStorage(read: { self.files[$0] }, write: { key, data in
                if self.failIndex && key.hasSuffix("index.json") { throw CocoaError(.fileWriteOutOfSpace) }
                self.files[key] = data
            }, remove: { self.files.removeValue(forKey: $0) }, eraseAccount: { digest in
                if self.failErase { throw CocoaError(.fileWriteNoPermission) }
                self.files = self.files.filter { !$0.key.hasPrefix(digest + "-") }
            })
        }
        var indices: [String: Data] { files.filter { $0.key.hasSuffix("index.json") } }
        var images: [String: Data] { files.filter { $0.key.hasSuffix(".jpg") } }
        func index() throws -> [String: Any] {
            let data = try XCTUnwrap(indices.values.first)
            return try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        }
    }

    private func defaults() -> UserDefaults {
        UserDefaults(suiteName: "OfflineRecipeTests-\(UUID().uuidString)")!
    }
    private func recipe(_ id: Int) throws -> Recipe {
        let value = """
        {"id":\(id),"name":"Rezept \(id)","isFavorite":false,"ingredients":[{"name":"Mehl","amount":200,"unit":"g"}],"steps":[{"instruction":"Alles vermischen.","timerSeconds":60}],"needsManualCare":false,"manualCareReasons":[],"servings":2}
        """
        return try JSONDecoder().decode(Recipe.self, from: Data(value.utf8))
    }
    private func context(namespace: String = "https://one.example\u{0}7", household: Int = 31,
                         householdError: Error? = nil, recipeError: Int? = nil,
                         onRecipe: ((Int) -> Void)? = nil) -> OfflineRecipeContext {
        OfflineRecipeContext(identity: UUID(), namespace: namespace, isCurrent: { true }, household: {
            if let householdError { throw householdError }; return household
        }, recipe: { id in
            onRecipe?(id)
            if id == recipeError { throw URLError(.networkConnectionLost) }
            return try self.recipe(id)
        }, image: { _ in Data("image-data".utf8) }, cookbookRecipes: { _ in [] })
    }

    func testFullDownloadPersistsIngredientsStepsAndImageAndRestoresOffline() async throws {
        let disk = Disk(), preferences = defaults()
        let store = OfflineRecipeStore(storage: disk.storage, defaults: preferences)
        let online = context()
        await store.activate(context: online)
        try await store.download(ids: [11], cookbook: nil, context: online)
        let document = try disk.index()
        let recipes = try XCTUnwrap(document["recipes"] as? [String: [String: Any]])
        let recipe = try XCTUnwrap(recipes["11"]?["recipe"] as? [String: Any])
        XCTAssertEqual((recipe["ingredients"] as? [[String: Any]])?.first?["name"] as? String, "Mehl")
        XCTAssertEqual((recipe["steps"] as? [[String: Any]])?.first?["instruction"] as? String, "Alles vermischen.")
        XCTAssertEqual(disk.images.count, 1)
        store.deactivate()
        let restored = OfflineRecipeStore(storage: disk.storage, defaults: preferences)
        await restored.activate(context: context(householdError: URLError(.notConnectedToInternet)))
        XCTAssertTrue(restored.isReady)
        XCTAssertNil(restored.errorMessage)
    }

    func testFailedCookbookDownloadCannotPublishPartialSuccessOrLeaveNewImages() async throws {
        let disk = Disk()
        let store = OfflineRecipeStore(storage: disk.storage, defaults: defaults())
        let current = context(recipeError: 12)
        await store.activate(context: current)
        try await store.download(ids: [11], cookbook: nil, context: current)
        let previous = disk.files
        do {
            try await store.download(ids: [11, 12], cookbook: HouseholdCookbook(id: 3, name: "Familie", recipeCount: 2, containsRecipe: false), context: current)
            XCTFail("A partly downloaded cookbook must fail")
        } catch { XCTAssertTrue(OfflineRecipeStore.isConnectivityFailure(error)) }
        XCTAssertEqual(disk.files, previous)
        XCTAssertFalse(store.isDownloading)
        XCTAssertNotNil(store.errorMessage)
    }

    func testIndexWriteFailurePreservesPreviousDownloadAndCleansStagedImage() async throws {
        let disk = Disk()
        let store = OfflineRecipeStore(storage: disk.storage, defaults: defaults())
        let current = context()
        await store.activate(context: current)
        try await store.download(ids: [11], cookbook: nil, context: current)
        let previous = disk.files
        disk.failIndex = true
        do { try await store.download(ids: [12], cookbook: nil, context: current); XCTFail("Disk full must fail") }
        catch { XCTAssertEqual((error as? CocoaError)?.code, .fileWriteOutOfSpace) }
        XCTAssertEqual(disk.files, previous)
        XCTAssertFalse(store.isDownloading)
    }

    func testUnauthorizedResponseNeverFallsBackToPreviouslyDownloadedHousehold() async throws {
        let disk = Disk(), preferences = defaults()
        let store = OfflineRecipeStore(storage: disk.storage, defaults: preferences)
        let current = context()
        await store.activate(context: current)
        try await store.download(ids: [11], cookbook: nil, context: current)
        await store.activate(context: context(householdError: APIError.unauthenticated))
        XCTAssertFalse(store.isReady)
        await store.activate(context: context(householdError: URLError(.notConnectedToInternet)))
        XCTAssertFalse(store.isReady, "An authentication rejection invalidates the offline pointer")
        XCTAssertEqual(disk.indices.count, 1, "A failed authorization never deletes another user's content")
    }

    func testServerUserAndHouseholdNamespacesStaySeparate() async throws {
        let disk = Disk(), preferences = defaults()
        let store = OfflineRecipeStore(storage: disk.storage, defaults: preferences)
        for current in [context(), context(household: 32), context(namespace: "https://two.example\u{0}7"), context(namespace: "https://one.example\u{0}8")] {
            await store.activate(context: current)
            try await store.download(ids: [11], cookbook: nil, context: current)
        }
        XCTAssertEqual(disk.indices.count, 4)
        XCTAssertEqual(disk.images.count, 4)
        try store.eraseAccountData(namespace: "https://one.example\u{0}7")
        XCTAssertEqual(disk.indices.count, 2, "Account erasure includes the account's former household")
        XCTAssertEqual(disk.images.count, 2)
    }

    func testHouseholdInvalidationBlocksOfflinePointerUntilOnlineVerification() async throws {
        let store = OfflineRecipeStore(storage: Disk().storage, defaults: defaults())
        await store.activate(context: context())
        try store.invalidateHousehold()
        await store.activate(context: context(householdError: URLError(.notConnectedToInternet)))
        XCTAssertFalse(store.isReady)
        await store.activate(context: context(household: 99))
        XCTAssertTrue(store.isReady)
    }

    func testLateRecipeResponseCannotWriteAfterAccountSwitch() async throws {
        let disk = Disk()
        let store = OfflineRecipeStore(storage: disk.storage, defaults: defaults())
        let current = context(onRecipe: { _ in store.deactivate() })
        await store.activate(context: current)
        do { try await store.download(ids: [11], cookbook: nil, context: current); XCTFail("Late response must fail") }
        catch { guard case APIError.sessionChanged = error else { return XCTFail("Wrong error: \(error)") } }
        XCTAssertTrue(disk.files.isEmpty)
        XCTAssertFalse(store.isReady)
        XCTAssertFalse(store.isDownloading)
    }

    func testFailedAccountErasePermanentlyPreventsRestoreOrNewDownload() async throws {
        let disk = Disk(), preferences = defaults()
        let store = OfflineRecipeStore(storage: disk.storage, defaults: preferences)
        let current = context()
        await store.activate(context: current)
        try await store.download(ids: [11], cookbook: nil, context: current)
        disk.failErase = true
        XCTAssertThrowsError(try store.eraseAccountData(namespace: current.namespace))
        XCTAssertFalse(store.isReady)
        let restored = OfflineRecipeStore(storage: disk.storage, defaults: preferences)
        await restored.activate(context: context())
        XCTAssertFalse(restored.isReady)
        XCTAssertEqual(disk.indices.count, 1, "Failure must be truthful; tombstone hides the remaining bytes")
    }

    func testOldActivationWaiterCannotCancelNewHouseholdActivation() async throws {
        let disk = Disk()
        let store = OfflineRecipeStore(storage: disk.storage, defaults: defaults())
        let firstGate = HouseholdGate(), secondGate = HouseholdGate()
        defer { firstGate.resume(31); secondGate.resume(99) }
        let firstStarted = expectation(description: "First household request started")
        let waiterStarted = expectation(description: "Second caller joined first activation")
        let secondStarted = expectation(description: "Replacement household request started")
        let base = context()
        var firstRequestCount = 0
        let first = OfflineRecipeContext(identity: base.identity, namespace: base.namespace, isCurrent: { true },
            household: {
                firstRequestCount += 1
                if firstRequestCount == 1 { return await firstGate.wait(started: firstStarted) }
                return 31
            }, recipe: base.recipe,
            image: base.image, cookbookRecipes: base.cookbookRecipes)
        let replacement = OfflineRecipeContext(identity: UUID(), namespace: base.namespace, isCurrent: { true },
            household: { await secondGate.wait(started: secondStarted) }, recipe: base.recipe,
            image: base.image, cookbookRecipes: base.cookbookRecipes)
        let initial = Task { await store.activate(context: first) }
        await fulfillment(of: [firstStarted], timeout: 2)
        let waiter = Task {
            waiterStarted.fulfill()
            await store.activate(context: first)
        }
        await fulfillment(of: [waiterStarted], timeout: 2)
        await Task.yield()
        try store.invalidateHousehold()
        let next = Task { await store.activate(context: replacement) }
        await fulfillment(of: [secondStarted], timeout: 2)
        // The old cancelled request completes while the new request is suspended.
        // Its joined caller must neither clear nor replace the new activation.
        firstGate.resume(31)
        await initial.value
        await waiter.value
        secondGate.resume(99)
        await next.value
        XCTAssertTrue(store.isReady)
        XCTAssertEqual(firstRequestCount, 1, "The stale waiter must not start the retired activation again")
        try await store.download(ids: [11], cookbook: nil, context: replacement)
        XCTAssertEqual(try disk.index()["householdID"] as? Int, 99)
        XCTAssertEqual(disk.indices.count, 1)
    }

    func testHouseholdChangeRetiresVisibleSessionWhenKeychainTokenDisappears() async throws {
        let preferences = defaults()
        preferences.set("https://example.de", forKey: "server-url")
        var token: String? = "household-transition-test-token"
        let persistence = LocalSessionPersistence(defaults: preferences, read: { token }, save: { token = $0 }, delete: { token = nil; return true })
        let session = SessionStore(api: APIClient(session: MockURLProtocol.makeSession()), defaults: preferences,
            persistence: persistence, sharedImportURLs: { [] }, removeSharedImport: { _ in })
        MockURLProtocol.respond(json: #"{"id":12,"username":"test","role":"user","name":"Rezepte","version":"1.12.0","capabilities":[],"ok":true}"#)
        await session.restore()
        guard case .signedIn = session.state else { return XCTFail("Fixture must start signed in") }
        let oldIdentity = session.identity
        let notified = expectation(forNotification: Notification.Name("KitchenHouseholdChanged"), object: nil)
        token = nil
        do { try await session.householdDidChange(); XCTFail("Missing token must fail the transition") }
        catch { guard case APIError.unauthenticated = error else { return XCTFail("Wrong transition error: \(error)") } }
        await fulfillment(of: [notified], timeout: 2)
        XCTAssertNotEqual(session.identity, oldIdentity, "RootView must retire private views even when Keychain fails")
        guard case .signedOut = session.state else { return XCTFail("A lost token must end the visible session") }
        XCTAssertNil(session.userID)
        XCTAssertNil(preferences.data(forKey: "offline-session-v1"))
        XCTAssertNotNil(session.alertMessage, "Transition failure must be visible, not filtered through the invalidated old cache")
        let request = try await session.api.imageRequest(recipeID: 11)
        XCTAssertNil(request.value(forHTTPHeaderField: "Authorization"), "The pre-transition API token must also be cleared")
    }

    func testOnlyConnectivityErrorsQualifyForOfflineFallback() {
        XCTAssertTrue(OfflineRecipeStore.isConnectivityFailure(URLError(.notConnectedToInternet)))
        XCTAssertFalse(OfflineRecipeStore.isConnectivityFailure(URLError(.cancelled)))
        XCTAssertFalse(OfflineRecipeStore.isConnectivityFailure(URLError(.serverCertificateUntrusted)))
        XCTAssertFalse(OfflineRecipeStore.isConnectivityFailure(APIError.unauthenticated))
        XCTAssertFalse(OfflineRecipeStore.isConnectivityFailure(APIError.server(403, "Forbidden")))
        XCTAssertFalse(OfflineRecipeStore.isConnectivityFailure(APIError.server(500, "Broken")))
    }

    func testImageValidationRejectsNonImagePayloadAndOversizedBytes() {
        XCTAssertThrowsError(try OfflineImageDownload.validatedJPEG(Data("<html>login</html>".utf8)))
        XCTAssertThrowsError(try OfflineImageDownload.validatedJPEG(Data(count: OfflineImageDownload.maximumBytes + 1)))
    }

    func testImageValidationPreservesARealImageAsBoundedJPEG() throws {
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 12, height: 10))
        let source = renderer.pngData { context in UIColor.red.setFill(); context.fill(CGRect(x: 0, y: 0, width: 12, height: 10)) }
        let jpeg = try OfflineImageDownload.validatedJPEG(source)
        XCTAssertNotNil(UIImage(data: jpeg))
        XCTAssertLessThan(jpeg.count, OfflineImageDownload.maximumBytes)
    }

    func testFileStorageRejectsPathTraversal() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let storage = OfflineRecipeStorage.files(directory: root)
        XCTAssertThrowsError(try storage.write("../../outside.json", Data()))
        XCTAssertThrowsError(try storage.read("../../outside.json"))
        XCTAssertThrowsError(try storage.eraseAccount("../"))
    }
}
