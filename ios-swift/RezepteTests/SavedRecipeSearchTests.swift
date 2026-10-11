import Foundation
import XCTest
@testable import Rezepte

@MainActor
final class SavedRecipeSearchTests: XCTestCase {
    private func storage() -> UserDefaults { UserDefaults(suiteName: "search-tests-\(UUID().uuidString)")! }

    func testAllFiltersRoundTripAndSearchRestores() throws {
        let defaults = storage()
        let store = SavedRecipeSearchStore(defaults: defaults)
        try store.activate(accountKey: "user-a", householdID: 7)
        var filters = RecipeFilters()
        filters.library = .mine
        filters.type = "Hauptgericht"; filters.category = "Pasta"
        filters.tagIDs = [3, 4]; filters.allergenTagIDs = [9]
        filters.includedIngredients = ["Tomate"]; filters.excludedIngredients = ["Erdnuss"]
        filters.favoriteOnly = true; filters.manualOnly = true; filters.minRating = 4
        try store.save(name: "  Abendessen  ", query: "Zitrone", filters: filters)
        let restored = SavedRecipeSearchStore(defaults: defaults)
        try restored.activate(accountKey: "user-a", householdID: 7)
        XCTAssertEqual(restored.items.first?.name, "Abendessen")
        XCTAssertEqual(restored.items.first?.query, "Zitrone")
        XCTAssertEqual(restored.items.first?.filters, filters)
    }

    func testAccountAndHouseholdIsolationAndRenameDelete() throws {
        let store = SavedRecipeSearchStore(defaults: storage())
        try store.activate(accountKey: "user-a", householdID: 7)
        try store.save(name: "Privat", query: "", filters: RecipeFilters())
        let id = try XCTUnwrap(store.items.first?.id)
        try store.rename(id: id, name: "Neu")
        try store.activate(accountKey: "user-b", householdID: 7)
        XCTAssertTrue(store.items.isEmpty)
        try store.activate(accountKey: "user-a", householdID: 8)
        XCTAssertTrue(store.items.isEmpty)
        try store.activate(accountKey: "user-a", householdID: 7)
        XCTAssertEqual(store.items.first?.name, "Neu")
        try store.remove(id: id)
        XCTAssertTrue(store.items.isEmpty)
    }

    func testErasurePreventsStaleWriterAndClearsFormerHouseholds() throws {
        let defaults = storage()
        let store = SavedRecipeSearchStore(defaults: defaults)
        try store.activate(accountKey: "user-a", householdID: 7)
        try store.save(name: "A", query: "private query", filters: RecipeFilters())
        let stale = SavedRecipeSearchStore(defaults: defaults)
        try stale.activate(accountKey: "user-a", householdID: 7)
        try store.activate(accountKey: "user-a", householdID: 8)
        try store.save(name: "B", query: "", filters: RecipeFilters())
        store.eraseAccountData(accountKey: "user-a")
        XCTAssertFalse(defaults.dictionaryRepresentation().keys.contains(where: { $0.hasPrefix("user-a-household") }))
        XCTAssertThrowsError(try stale.save(name: "Resurrect", query: "", filters: RecipeFilters()))
        XCTAssertThrowsError(try store.activate(accountKey: "user-a", householdID: 7))
    }

    func testContextChangeAndInvalidNameDoNotWrite() throws {
        let store = SavedRecipeSearchStore(defaults: storage())
        var current = true
        try store.activate(accountKey: "a", householdID: 1, isCurrent: { current })
        XCTAssertThrowsError(try store.save(name: " ", query: "", filters: RecipeFilters()))
        XCTAssertThrowsError(try store.save(name: String(repeating: "a", count: 81), query: "", filters: RecipeFilters()))
        current = false
        XCTAssertThrowsError(try store.save(name: "Late write", query: "", filters: RecipeFilters()))
        XCTAssertTrue(store.items.isEmpty)
    }

    func testHouseholdInvalidationRemovesOfflinePointer() throws {
        let defaults = storage()
        let store = SavedRecipeSearchStore(defaults: defaults)
        try store.activate(accountKey: "a", householdID: 1)
        store.invalidateHousehold()
        XCTAssertNil(defaults.object(forKey: "a-household"))
        XCTAssertFalse(store.isReady)
    }

    func testServerNamespacesUseCanonicalURLAndUserID() {
        XCTAssertEqual(SavedRecipeSearchStore.accountKey(server: "https://example.com/", userID: 1),
                       SavedRecipeSearchStore.accountKey(server: "https://example.com", userID: 1))
        XCTAssertNotEqual(SavedRecipeSearchStore.accountKey(server: "https://example.com", userID: 1),
                          SavedRecipeSearchStore.accountKey(server: "https://example.com", userID: 2))
        XCTAssertNotEqual(SavedRecipeSearchStore.accountKey(server: "https://example.com", userID: 1),
                          SavedRecipeSearchStore.accountKey(server: "https://other.example.com", userID: 1))
    }
}
