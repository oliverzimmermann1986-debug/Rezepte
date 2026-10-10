import Foundation
import XCTest
@testable import Rezepte

final class KitchenFeatureTests: XCTestCase {
    func testTimerSecondsClampBeforeConvertingToInteger() {
        for (value, expected) in [(Double.nan, 0), (.infinity, 0), (-.infinity, 0), (-5, 0), (0.1, 1), (86401, 86400), (Double.greatestFiniteMagnitude, 86400)] {
            let timer = KitchenTimerEntry(id: "1-2", label: "Test", duration: 60, remaining: value, endsAt: nil)
            XCTAssertEqual(timer.seconds(), expected)
        }
        let now = Date(timeIntervalSince1970: 100)
        let running = KitchenTimerEntry(id: "1-2", label: "Test", duration: 60, remaining: 60, endsAt: now.addingTimeInterval(5.2))
        XCTAssertEqual(running.seconds(at: now), 6)
        XCTAssertEqual(running.seconds(at: now.addingTimeInterval(10)), 0)
    }

    func testVoiceAndTouchUsePersistedStepIdentity() {
        XCTAssertEqual(KitchenTimerEntry.identifier(recipeID: 2, stepID: 45, index: 0), "2-45")
        XCTAssertEqual(KitchenTimerEntry.identifier(recipeID: 2, stepID: nil, index: 3), "2-3")
    }

    func testVoiceCommandsRejectUnrelatedRecipeSpeech() {
        XCTAssertEqual(CookingVoiceCommand.parse("Nächster Schritt!"), .next)
        XCTAssertEqual(CookingVoiceCommand.parse("Timer pausieren"), .pauseTimer)
        XCTAssertEqual(CookingVoiceCommand.parse("Sprachsteuerung ausschalten"), .stop)
        XCTAssertNil(CookingVoiceCommand.parse("Jetzt weiter mit dem Gemüse"))
        XCTAssertNil(CookingVoiceCommand.parse("Timer in 20 Minuten starten"))
        XCTAssertNil(CookingVoiceCommand.parse(""))
    }

    func testCookingCompletionDecodesActualNestedHistoryEntry() throws {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        let result = try decoder.decode(CookingCompletionResult.self, from: Data(#"{"ok":true,"entry":{"id":42,"recipe_id":7,"cooked_by":"Test","servings":3,"cooked_at":1730000000},"summary":{"count":1}}"#.utf8))
        XCTAssertEqual(result.completedHistoryID, 42)
        let legacy = try decoder.decode(CookingCompletionResult.self, from: Data(#"{"ok":true,"history_id":9}"#.utf8))
        XCTAssertEqual(legacy.completedHistoryID, 9)
    }

    @MainActor
    func testRemovingTimerWhilePermissionIsPendingCannotResurrectIt() async throws {
        let fixture = try TimerFixture()
        defer { fixture.cleanUp() }
        let requested = expectation(description: "Permission pending")
        var continuation: CheckedContinuation<Bool, Never>?
        let store = fixture.store {
            await withCheckedContinuation { continuation = $0; requested.fulfill() }
        }
        store.activate(identity: UUID(), server: "https://example.de", userID: 7, writable: true)
        let start = Task { await store.start(id: "7-1", seconds: 60, label: "Test") }
        await fulfillment(of: [requested], timeout: 2)
        store.remove(id: "7-1")
        continuation?.resume(returning: true)
        await start.value
        XCTAssertTrue(store.entries.isEmpty)
        XCTAssertTrue(store.startingIDs.isEmpty)
    }

    @MainActor
    func testSessionChangeRejectsPendingTimerStart() async throws {
        let fixture = try TimerFixture()
        defer { fixture.cleanUp() }
        let requested = expectation(description: "Old session permission pending")
        var continuation: CheckedContinuation<Bool, Never>?
        let store = fixture.store {
            await withCheckedContinuation { continuation = $0; requested.fulfill() }
        }
        store.activate(identity: UUID(), server: "https://example.de", userID: 7, writable: true)
        let start = Task { await store.start(id: "7-1", seconds: 60, label: "Privat") }
        await fulfillment(of: [requested], timeout: 2)
        store.activate(identity: UUID(), server: "https://example.de", userID: 8, writable: true)
        continuation?.resume(returning: true)
        await start.value
        XCTAssertTrue(store.entries.isEmpty)
    }

    @MainActor
    func testTimerPersistsPauseAndClearTombstone() async throws {
        let fixture = try TimerFixture()
        defer { fixture.cleanUp() }
        let first = fixture.store { false }
        first.activate(identity: UUID(), server: "https://example.de", userID: 7, writable: true)
        await first.start(id: "7-1", seconds: 60, label: "Test")
        XCTAssertEqual(first.entries.count, 1, "Denied notifications must not discard the timer")
        first.pause(id: "7-1")
        XCTAssertNil(first.entries.first?.endsAt)
        let restored = fixture.store { false }
        restored.activate(identity: UUID(), server: "https://example.de", userID: 7, writable: true)
        XCTAssertEqual(restored.entries.count, 1)
        XCTAssertNil(restored.entries.first?.endsAt)
        restored.clear()
        let afterSignOut = fixture.store { false }
        afterSignOut.activate(identity: UUID(), server: "https://example.de", userID: 7, writable: true)
        XCTAssertTrue(afterSignOut.entries.isEmpty)
    }
}

@MainActor
private struct TimerFixture {
    let suite: String
    let directory: URL
    let defaults: UserDefaults
    init() throws {
        let name = "KitchenFeatureTests-\(UUID())"
        suite = name
        directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defaults = try XCTUnwrap(UserDefaults(suiteName: name))
    }
    func store(authorize: @escaping () async -> Bool) -> KitchenTimerStore {
        KitchenTimerStore(defaults: defaults, directory: directory, requestAuthorization: authorize, systemEffects: false)
    }
    func cleanUp() {
        defaults.removePersistentDomain(forName: suite)
        try? FileManager.default.removeItem(at: directory)
    }
}
