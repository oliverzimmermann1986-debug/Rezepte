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
        var failRemove = false
        var storage: OfflineRecipeStorage {
            OfflineRecipeStorage(read: { self.files[$0] }, write: { key, data in
                if self.failIndex && key.hasSuffix("index.json") { throw CocoaError(.fileWriteOutOfSpace) }
                self.files[key] = data
            }, remove: {
                if self.failRemove { throw CocoaError(.fileWriteNoPermission) }
                self.files.removeValue(forKey: $0)
            }, eraseAccount: { digest in
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

    func testConfirmedRecipeDeletionRemovesItsProgressAndBooksButPreservesOtherDownloads() async throws {
        let disk = Disk()
        let store = OfflineRecipeStore(storage: disk.storage, defaults: defaults())
        let current = context()
        await store.activate(context: current)
        try await store.download(ids: [11, 12], cookbook: HouseholdCookbook(id: 3, name: "Familie", recipeCount: 2, containsRecipe: false), context: current)
        try await store.download(ids: [13], cookbook: HouseholdCookbook(id: 4, name: "Weitere", recipeCount: 1, containsRecipe: false), context: current)
        try store.saveCookingProgress(id: 11, value: OfflineCookingProgress(completedSteps: [0], activeStep: 0, servings: 2), context: current)
        try store.recordDeletedRecipe(id: 11, context: current)
        XCTAssertNil(store.cachedRecipe(id: 11, context: current))
        XCTAssertNil(store.cookingProgress(id: 11, context: current))
        XCTAssertNotNil(store.cachedRecipe(id: 12, context: current))
        XCTAssertNotNil(store.cachedRecipe(id: 13, context: current))
        let books = try XCTUnwrap(disk.index()["cookbooks"] as? [String: Any])
        XCTAssertNil(books["3"], "A partial cookbook cannot remain marked as fully downloaded")
        XCTAssertNotNil(books["4"])
        XCTAssertEqual(disk.images.count, 2)
        // A later cookbook transaction must not prune the retained sibling.
        try await store.download(ids: [14], cookbook: HouseholdCookbook(id: 5, name: "Neu", recipeCount: 1, containsRecipe: false), context: current)
        XCTAssertNotNil(store.cachedRecipe(id: 12, context: current))
    }

    func testRecipeDeletionRejectsAlreadyRunningDownloadResponse() async throws {
        let disk = Disk()
        let store = OfflineRecipeStore(storage: disk.storage, defaults: defaults())
        let current = context()
        await store.activate(context: current)
        try await store.download(ids: [11, 12], cookbook: nil, context: current)
        let gate = HouseholdGate()
        defer { gate.resume(31) }
        let started = expectation(description: "Old recipe download is waiting for the server")
        let delayed = OfflineRecipeContext(identity: current.identity, namespace: current.namespace, isCurrent: current.isCurrent,
            household: current.household, recipe: { id in
                _ = await gate.wait(started: started)
                return try await current.recipe(id)
            }, image: current.image, cookbookRecipes: current.cookbookRecipes)
        let flight = Task { try await store.download(ids: [11], cookbook: nil, context: delayed) }
        await fulfillment(of: [started], timeout: 2)
        try store.recordDeletedRecipe(id: 11, context: current)
        gate.resume(31)
        do { try await flight.value; XCTFail("A response that predates deletion must not restore the recipe") }
        catch { guard case APIError.sessionChanged = error else { return XCTFail("Wrong late-response error: \(error)") } }
        XCTAssertNil(store.cachedRecipe(id: 11, context: current))
        XCTAssertNotNil(store.cachedRecipe(id: 12, context: current))
        XCTAssertEqual(disk.images.count, 1)
        XCTAssertFalse(store.isDownloading)
    }

    func testRecipeDeletionWriteFailureStaysHiddenAfterRestartUntilNewSuccessfulDownload() async throws {
        let disk = Disk(), preferences = defaults()
        let store = OfflineRecipeStore(storage: disk.storage, defaults: preferences)
        let current = context()
        await store.activate(context: current)
        try await store.download(ids: [11, 12], cookbook: HouseholdCookbook(id: 3, name: "Familie", recipeCount: 2, containsRecipe: false), context: current)
        try store.saveCookingProgress(id: 11, value: OfflineCookingProgress(completedSteps: [0], activeStep: 0, servings: 2), context: current)
        disk.failIndex = true
        XCTAssertThrowsError(try store.recordDeletedRecipe(id: 11, context: current)) { error in
            XCTAssertTrue(error.localizedDescription.contains("auf dem Server gelöscht"))
            XCTAssertTrue(error.localizedDescription.contains("Bereinigung ist unvollständig"))
        }
        XCTAssertNil(store.cachedRecipe(id: 11, context: current))
        XCTAssertNil(store.cookingProgress(id: 11, context: current))
        let oldRecords = try XCTUnwrap(disk.index()["recipes"] as? [String: Any])
        XCTAssertNotNil(oldRecords["11"], "The disk failure deliberately leaves the stale index bytes")
        let restarted = OfflineRecipeStore(storage: disk.storage, defaults: preferences)
        let restoredContext = context(householdError: URLError(.notConnectedToInternet))
        await restarted.activate(context: restoredContext)
        XCTAssertTrue(restarted.isReady)
        XCTAssertNil(restarted.cachedRecipe(id: 11, context: restoredContext))
        XCTAssertNil(restarted.cookingProgress(id: 11, context: restoredContext))
        XCTAssertNotNil(restarted.cachedRecipe(id: 12, context: restoredContext))
        do { try await restarted.download(ids: [11], cookbook: nil, context: restoredContext); XCTFail("A failed replacement must not lift the marker") }
        catch { XCTAssertEqual((error as? CocoaError)?.code, .fileWriteOutOfSpace) }
        XCTAssertNil(restarted.cachedRecipe(id: 11, context: restoredContext))
        disk.failIndex = false
        try await restarted.download(ids: [11], cookbook: nil, context: restoredContext)
        XCTAssertNotNil(restarted.cachedRecipe(id: 11, context: restoredContext))
        let afterRestore = OfflineRecipeStore(storage: disk.storage, defaults: preferences)
        let finalContext = context(householdError: URLError(.notConnectedToInternet))
        await afterRestore.activate(context: finalContext)
        XCTAssertNotNil(afterRestore.cachedRecipe(id: 11, context: finalContext))
        XCTAssertNotNil(afterRestore.cachedRecipe(id: 12, context: finalContext))
        let books = try XCTUnwrap(disk.index()["cookbooks"] as? [String: Any])
        XCTAssertTrue(books.isEmpty, "Restoring one recipe does not restore the former cookbook download")
    }

    func testDeletedRecipeImageCleanupFailureReportsPartialCleanupButHidesRecipe() async throws {
        let disk = Disk(), preferences = defaults()
        let store = OfflineRecipeStore(storage: disk.storage, defaults: preferences)
        let current = context()
        await store.activate(context: current)
        try await store.download(ids: [11], cookbook: nil, context: current)
        disk.failRemove = true
        XCTAssertThrowsError(try store.recordDeletedRecipe(id: 11, context: current))
        XCTAssertNil(store.cachedRecipe(id: 11, context: current))
        XCTAssertEqual(disk.images.count, 1, "Report failure truthfully while preventing the remaining image from being referenced")
        let restored = OfflineRecipeStore(storage: disk.storage, defaults: preferences)
        let restoredContext = context(householdError: URLError(.notConnectedToInternet))
        await restored.activate(context: restoredContext)
        XCTAssertNil(restored.cachedRecipe(id: 11, context: restoredContext))
        XCTAssertTrue(restored.isReady)
    }

    func testLateDeleteAcknowledgementOnlyCleansCapturedOriginNamespace() async throws {
        let disk = Disk(), preferences = defaults()
        let store = OfflineRecipeStore(storage: disk.storage, defaults: preferences)
        let origin = context()
        await store.activate(context: origin)
        try await store.download(ids: [11, 12], cookbook: nil, context: origin)
        let other = context(namespace: "https://two.example\u{0}8", household: 99)
        await store.activate(context: other)
        try await store.download(ids: [11], cookbook: nil, context: other)
        let otherFiles = disk.files.filter { $0.key.contains("-household-99-") }
        try store.recordDeletedRecipe(id: 11, namespace: origin.namespace)
        XCTAssertTrue(store.isReady)
        XCTAssertNotNil(store.cachedRecipe(id: 11, context: other))
        XCTAssertNil(store.errorMessage)
        XCTAssertEqual(disk.files.filter { $0.key.contains("-household-99-") }, otherFiles)
        let originRestore = context(householdError: URLError(.notConnectedToInternet))
        await store.activate(context: originRestore)
        XCTAssertNil(store.cachedRecipe(id: 11, context: originRestore))
        XCTAssertNotNil(store.cachedRecipe(id: 12, context: originRestore))
    }

    func testLateDeleteDiskFailureDoesNotTouchActiveOtherAccount() async throws {
        let disk = Disk(), preferences = defaults()
        let store = OfflineRecipeStore(storage: disk.storage, defaults: preferences)
        let origin = context()
        await store.activate(context: origin)
        try await store.download(ids: [11], cookbook: nil, context: origin)
        let other = context(namespace: "https://two.example\u{0}8", household: 99)
        await store.activate(context: other)
        try await store.download(ids: [11], cookbook: nil, context: other)
        disk.failIndex = true
        XCTAssertThrowsError(try store.recordDeletedRecipe(id: 11, namespace: origin.namespace))
        XCTAssertNotNil(store.cachedRecipe(id: 11, context: other))
        XCTAssertTrue(store.isReady)
        XCTAssertNil(store.errorMessage, "An old account's cleanup must not overwrite the current account's feedback")
        let restoredOrigin = context(householdError: URLError(.notConnectedToInternet))
        await store.activate(context: restoredOrigin)
        XCTAssertNil(store.cachedRecipe(id: 11, context: restoredOrigin))
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
