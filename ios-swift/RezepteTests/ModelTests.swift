import XCTest
@testable import Rezepte

final class ModelTests: XCTestCase {
    func testIngredientDisplayTextOmitsMissingValues() {
        let ingredient = Ingredient(
            id: nil,
            name: "Salz",
            amount: nil,
            unit: nil,
            canonicalName: "salz"
        )
        XCTAssertEqual(ingredient.displayText, "Salz")
    }

    func testIngredientDisplayTextFormatsWholeAmount() {
        let ingredient = Ingredient(
            id: 1,
            name: "Tomaten",
            amount: 2,
            unit: "Stück",
            canonicalName: "tomate"
        )
        XCTAssertEqual(ingredient.displayText, "2 Stück Tomaten")
    }

    func testRecipeFilterCountsEveryActiveSelection() {
        var filters = RecipeFilters()
        filters.type = "Hauptgericht"
        filters.tagIDs = [1, 2]
        filters.includedIngredients = ["tomate"]
        filters.excludedIngredients = ["zwiebel"]
        filters.favoriteOnly = true
        filters.minRating = 3
        filters.manualOnly = true

        XCTAssertEqual(filters.activeCount, 8)
    }

    func testPendingImportDecodesEditableSuggestion() throws {
        let json = """
        {
          "url": "manual-upload://abc/rezept.jpg",
          "content_type": "recipe",
          "description": "Erkannter Rezepttext",
          "status": "pending",
          "ai_suggestion": {
            "name": "Unbekannt",
            "type": "Hauptgericht",
            "category": "Allgemein",
            "confidence": 0.2,
            "filename": "rezept.jpg",
            "source": "manual-upload"
          }
        }
        """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let item = try decoder.decode(PendingItem.self, from: Data(json.utf8))

        XCTAssertEqual(item.aiSuggestion?.filename, "rezept.jpg")
        XCTAssertEqual(item.aiSuggestion?.category, "Allgemein")
        XCTAssertEqual(item.displayName, "Unbekannt")
    }
}
