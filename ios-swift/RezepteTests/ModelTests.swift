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
}

