import XCTest
@testable import Rezepte

final class ModelTests: XCTestCase {
    func testSessionDecodesGuestReadOnlyAccess() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let session = try decoder.decode(
            SessionResponse.self,
            from: Data(#"{"username":"Gast","full_access":false,"read_only":true}"#.utf8)
        )

        XCTAssertEqual(session.username, "Gast")
        XCTAssertEqual(session.fullAccess, false)
        XCTAssertEqual(session.readOnly, true)
    }

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
        filters.allergenTagIDs = [7, 8]
        filters.includedIngredients = ["tomate"]
        filters.excludedIngredients = ["zwiebel"]
        filters.favoriteOnly = true
        filters.minRating = 3
        filters.manualOnly = true

        XCTAssertEqual(filters.activeCount, 10)
    }

    func testAllergenInfoRecognizesOnlySupportedFreeFromTags() {
        XCTAssertEqual(TagFacet(id: 1, name: "Glutenfrei", n: 12).allergenInfo, .glutenFree)
        XCTAssertEqual(TagFacet(id: 2, name: " laktosefrei ", n: 8).allergenInfo, .lactoseFree)
        XCTAssertEqual(TagFacet(id: 3, name: "eifrei", n: 5).allergenInfo, .eggFree)
        XCTAssertEqual(TagFacet(id: 4, name: "nussfrei", n: 7).allergenInfo, .nutFree)
        XCTAssertNil(TagFacet(id: 5, name: "vegan", n: 10).allergenInfo)
        XCTAssertNil(TagFacet(id: 6, name: "zuckerfrei", n: 3).allergenInfo)
    }

    func testRecipeDetailDecodesLegacySQLiteBoolean() throws {
        let json = """
        {
          "id": 232,
          "name": "Burrata",
          "is_favorite": false,
          "rating": 0,
          "ingredients": [],
          "steps": [],
          "needs_manual_care": true,
          "manual_care_reasons": [],
          "user_verified": 1
        }
        """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase

        let recipe = try decoder.decode(Recipe.self, from: Data(json.utf8))

        XCTAssertEqual(recipe.id, 232)
        XCTAssertEqual(recipe.userVerified, true)
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

    func testSourceIntegrityDecodesChangedSourceAndQualityIssues() throws {
        let json = """
        {
          "recipe_id": 42,
          "recipe_name": "Quellenpasta",
          "source_url": "https://example.de/pasta",
          "platform": "Webseite",
          "status": "changed",
          "checked_at": 12,
          "baseline": {
            "id": 1,
            "source_url": "https://example.de/pasta",
            "content_sha256": "old",
            "preview": "200 g Mehl",
            "checked_at": 10,
            "state": "baseline",
            "is_baseline": true
          },
          "latest": {
            "id": 2,
            "source_url": "https://example.de/pasta",
            "content_sha256": "new",
            "preview": "250 g Mehl",
            "checked_at": 12,
            "state": "changed",
            "is_baseline": false
          },
          "diff": {
            "changed": true,
            "added_lines": 1,
            "removed_lines": 1,
            "baseline_lines": 1,
            "current_lines": 1,
            "similarity": 0.5,
            "lines": ["-200 g Mehl", "+250 g Mehl"],
            "truncated": false
          },
          "quality": {
            "status": "review",
            "score": 88,
            "issues": [{
              "id": "source-changed",
              "title": "Originalquelle wurde verändert",
              "detail": "Bitte prüfen.",
              "severity": "warning",
              "section": "source"
            }],
            "checked_rules": 8
          },
          "verified": true,
          "verified_at": 9,
          "verified_by": "anna",
          "automatic_overwrite": false
        }
        """
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase

        let report = try decoder.decode(RecipeSourceIntegrity.self, from: Data(json.utf8))

        XCTAssertEqual(report.status, "changed")
        XCTAssertEqual(report.diff?.addedLines, 1)
        XCTAssertEqual(report.quality.issues.first?.id, "source-changed")
        XCTAssertFalse(report.automaticOverwrite)
    }
}
