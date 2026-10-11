import Foundation
import XCTest
@testable import Rezepte

@MainActor
final class MealPlanningProfileTests: XCTestCase {
    private func storage() -> UserDefaults { UserDefaults(suiteName: "meal-profile-tests-\(UUID().uuidString)")! }
    private func preferences() -> MealPlanningPreferences {
        var value = MealPlanningPreferences(count: 7, vegetarian: 3, maxMinutes: 90)
        value.constraints.counts = [.pasta: 2, .potato: 4, .rice: 0]
        value.constraints.excludedIngredientsText = "Pilze, Sellerie"
        value.constraints.avoidRecentDays = 14
        value.constraints.weekdayRules = [MealWeekdayRule(weekday: 1, maxMinutes: 20),
            MealWeekdayRule(weekday: 5, category: .pasta, vegetarian: true),
            MealWeekdayRule(weekday: 7, minMinutes: 60, maxMinutes: 90)]
        return value
    }

    func testAllPreferencesRoundTripWithoutBindingToAWeek() throws {
        let defaults = storage()
        let store = MealPlanningProfileStore(defaults: defaults)
        try store.activate(accountKey: "a", householdID: 1)
        let value = preferences()
        try store.save(name: "  Familienwoche  ", preferences: value)
        let restored = MealPlanningProfileStore(defaults: defaults)
        try restored.activate(accountKey: "a", householdID: 1)
        let profile = try XCTUnwrap(restored.items.first)
        XCTAssertEqual(profile.name, "Familienwoche")
        XCTAssertEqual(try restored.preferences(id: profile.id), value)
        XCTAssertEqual(profile.preferences.constraints.counts[.rice], 0)
        XCTAssertEqual(profile.preferences.constraints.weekdayRules.last?.minMinutes, 60)
    }

    func testAccountsHouseholdsRenameAndDeleteRemainSeparate() throws {
        let store = MealPlanningProfileStore(defaults: storage())
        try store.activate(accountKey: "a", householdID: 1)
        try store.save(name: "A", preferences: preferences())
        let id = try XCTUnwrap(store.items.first?.id)
        try store.rename(id: id, name: "  Neu  ")
        try store.activate(accountKey: "b", householdID: 1)
        XCTAssertTrue(store.items.isEmpty)
        XCTAssertThrowsError(try store.preferences(id: id))
        try store.activate(accountKey: "a", householdID: 2)
        XCTAssertTrue(store.items.isEmpty)
        try store.activate(accountKey: "a", householdID: 1)
        XCTAssertEqual(store.items.first?.name, "Neu")
        try store.remove(id: id)
        try store.activate(accountKey: "a", householdID: 1)
        XCTAssertTrue(store.items.isEmpty)
    }

    func testErasureRejectsStaleWriterAndRemovesAllHouseholds() throws {
        let defaults = storage()
        let store = MealPlanningProfileStore(defaults: defaults)
        try store.activate(accountKey: "a", householdID: 1)
        try store.save(name: "A", preferences: preferences())
        let stale = MealPlanningProfileStore(defaults: defaults)
        try stale.activate(accountKey: "a", householdID: 1)
        let id = try XCTUnwrap(stale.items.first?.id)
        try store.activate(accountKey: "a", householdID: 2)
        try store.save(name: "B", preferences: preferences())
        store.eraseAccountData(accountKey: "a")
        XCTAssertFalse(defaults.dictionaryRepresentation().keys.contains { $0.hasPrefix("a-household") })
        XCTAssertThrowsError(try stale.save(name: "Late", preferences: preferences()))
        XCTAssertThrowsError(try stale.preferences(id: id))
        XCTAssertThrowsError(try store.activate(accountKey: "a", householdID: 1))
    }

    func testInvalidatedContextRejectsApplyAndWrites() throws {
        let store = MealPlanningProfileStore(defaults: storage())
        var current = true
        try store.activate(accountKey: "a", householdID: 1, isCurrent: { current })
        try store.save(name: "A", preferences: preferences())
        let id = try XCTUnwrap(store.items.first?.id)
        current = false
        XCTAssertThrowsError(try store.preferences(id: id))
        XCTAssertThrowsError(try store.rename(id: id, name: "Late"))
        XCTAssertThrowsError(try store.remove(id: id))
        XCTAssertEqual(store.items.count, 1)
        store.deactivate()
        XCTAssertFalse(store.isReady)
        XCTAssertTrue(store.items.isEmpty)
        XCTAssertThrowsError(try store.save(name: "After logout", preferences: preferences()))
    }

    func testHouseholdInvalidationDropsVerifiedOfflinePointer() throws {
        let defaults = storage()
        let store = MealPlanningProfileStore(defaults: defaults)
        try store.activate(accountKey: "a", householdID: 1)
        store.invalidateHousehold()
        XCTAssertNil(defaults.object(forKey: "a-household"))
        XCTAssertFalse(store.isReady)
    }

    func testServerAndUserNamespacesAreCanonicalAndDistinct() {
        XCTAssertEqual(MealPlanningProfileStore.accountKey(server: "https://example.com/", userID: 1),
                       MealPlanningProfileStore.accountKey(server: "https://example.com", userID: 1))
        XCTAssertNotEqual(MealPlanningProfileStore.accountKey(server: "https://example.com", userID: 1),
                          MealPlanningProfileStore.accountKey(server: "https://example.com", userID: 2))
        XCTAssertNotEqual(MealPlanningProfileStore.accountKey(server: "https://example.com", userID: 1),
                          MealPlanningProfileStore.accountKey(server: "https://other.example.com", userID: 1))
    }

    func testInvalidProfilesCannotBeSavedOrApplied() throws {
        let defaults = storage()
        let store = MealPlanningProfileStore(defaults: defaults)
        try store.activate(accountKey: "a", householdID: 1)
        var value = preferences(); value.count = 2
        XCTAssertThrowsError(try store.save(name: "Invalid", preferences: value))
        XCTAssertThrowsError(try store.save(name: " ", preferences: preferences()))
        XCTAssertThrowsError(try store.save(name: String(repeating: "x", count: 81), preferences: preferences()))
        struct Document: Encodable { let version = 1; let items: [MealPlanningProfile] }
        let invalid = MealPlanningProfile(id: UUID(), name: "Old invalid profile", preferences: value)
        defaults.set(try JSONEncoder().encode(Document(items: [invalid])), forKey: "a-household-1")
        try store.activate(accountKey: "a", householdID: 1)
        XCTAssertEqual(store.items.count, 1)
        XCTAssertThrowsError(try store.preferences(id: invalid.id))
        try store.remove(id: invalid.id)
        XCTAssertTrue(store.items.isEmpty)
    }

    func testMissingServerCapabilitiesDoNotSilentlyDiscardPreferences() {
        let value = preferences()
        XCTAssertNotNil(value.availabilityMessage(advanced: false, weekdayRules: false))
        XCTAssertNotNil(value.availabilityMessage(advanced: true, weekdayRules: false))
        XCTAssertNil(value.availabilityMessage(advanced: true, weekdayRules: true))
        XCTAssertNil(MealPlanningPreferences().availabilityMessage(advanced: false, weekdayRules: false))
    }
}
