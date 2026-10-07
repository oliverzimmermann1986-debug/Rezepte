import Foundation
import XCTest
@testable import Rezepte

final class ModelsTests: XCTestCase {
    private func item(amount: Double? = 4, unit: String? = "Stück",
                      amountBase: Double? = 4, unitBase: String? = "Stück") -> CartItem {
        CartItem(id: 42, name: "Toilettenpapier", amount: amount, unit: unit,
                 checked: true, category: "Haushalt", icon: nil,
                 amountBase: amountBase, unitBase: unitBase)
    }

    func testCartItemDecodesDisplayAndStoredAmountsSeparately() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let json = #"{"id":42,"name":"Mehl","amount":1.5,"amount_base":1499,"unit":"kg","unit_base":"g","checked":true}"#
        let decoded = try decoder.decode(CartItem.self, from: Data(json.utf8))
        XCTAssertEqual(decoded.amount, 1.5)
        XCTAssertEqual(decoded.amountBase, 1499)
        XCTAssertEqual(decoded.unit, "kg")
        XCTAssertEqual(decoded.unitBase, "g")
        XCTAssertTrue(decoded.checked)
    }

    func testCartItemKeepsCompatibilityWithMissingBaseMetadata() throws {
        let json = #"{"id":42,"name":"Toilettenpapier","amount":4,"unit":"Stück","checked":false}"#
        let decoded = try JSONDecoder().decode(CartItem.self, from: Data(json.utf8))
        XCTAssertNil(decoded.amountBase)
        XCTAssertNil(decoded.unitBase)
        XCTAssertEqual(try decoded.baseAmount(forDisplayAmountText: "2"), 2)
    }

    func testFourPiecesCanBecomeTwoWithoutChangingItemState() throws {
        let existing = item()
        XCTAssertEqual(try existing.baseAmount(forDisplayAmountText: " 2\n"), 2)
        XCTAssertEqual(existing.amount, 4)
        XCTAssertEqual(existing.unit, "Stück")
        XCTAssertEqual(existing.name, "Toilettenpapier")
        XCTAssertTrue(existing.checked)
    }

    func testPhysicalAmountsUseFixedUnitFactorsIncludingGermanDecimals() throws {
        let cases: [(String, String, String, Double)] = [
            ("kg", "g", "2", 2000), ("g", "g", "250,5", 250.5),
            ("mg", "g", "250", 0.25), ("l", "ml", "1,25", 1250),
            ("ml", "ml", "50", 50), ("cl", "ml", "2", 20),
            ("dl", "ml", "2", 200),
        ]
        for (displayUnit, storedUnit, text, expected) in cases {
            let existing = item(unit: displayUnit, unitBase: storedUnit)
            XCTAssertEqual(try existing.baseAmount(forDisplayAmountText: text), expected,
                           accuracy: 1e-10, displayUnit)
        }
    }

    func testRoundedDisplayAndOldZeroDoNotDetermineConversionRatio() throws {
        let rounded = item(amount: 1.5, unit: "kg", amountBase: 1499, unitBase: "g")
        XCTAssertEqual(try rounded.baseAmount(forDisplayAmountText: "2"), 2000)
        let zero = item(amount: 0, unit: "mg", amountBase: 0, unitBase: "g")
        XCTAssertEqual(try zero.baseAmount(forDisplayAmountText: "250"), 0.25)
        let unknown = item(amount: nil, unit: "g", amountBase: nil, unitBase: "g")
        XCTAssertEqual(try unknown.baseAmount(forDisplayAmountText: "200"), 200)
    }

    func testSpoonsCountsAndUnitlessEntriesStayOneToOne() throws {
        for unit in ["Stück", "TL", "EL", "Pck", "Becher", "Rolle", nil] as [String?] {
            XCTAssertEqual(try item(unit: unit, unitBase: unit).baseAmount(forDisplayAmountText: "2,5"), 2.5)
        }
    }

    func testInvalidAndMissingAmountsCannotBeSavedAsNullOrZero() {
        for text in ["", "   ", "0", "-2", "NaN", "inf", "-inf", "1e309", "1e-400", "zwei", "1,2,3"] {
            XCTAssertThrowsError(try item().baseAmount(forDisplayAmountText: text), text) { error in
                XCTAssertEqual(error as? CartAmountError, .invalidAmount)
            }
        }
    }

    func testUnitMismatchAndAmbiguousPhysicalBaseAreRejected() {
        let cases: [(String?, String?)] = [
            ("kg", "ml"), ("l", "g"), ("kg", nil), ("kg", "kg"),
            ("Stück", "g"), ("Stück", "Pck"), (nil, "g"),
        ]
        for (displayUnit, storedUnit) in cases {
            XCTAssertThrowsError(try item(unit: displayUnit, unitBase: storedUnit)
                .baseAmount(forDisplayAmountText: "2")) { error in
                XCTAssertEqual(error as? CartAmountError, .incompatibleUnits)
            }
        }
    }

    func testConversionOverflowAndUnderflowAreRejected() {
        for (unit, text) in [("kg", "1e308"), ("mg", "1e-323")] {
            XCTAssertThrowsError(try item(unit: unit, unitBase: "g")
                .baseAmount(forDisplayAmountText: text)) { error in
                XCTAssertEqual(error as? CartAmountError, .invalidAmount)
            }
        }
    }

    func testLargeFiniteCartAmountCanBeDisplayedWithoutIntegerOverflow() {
        let existing = item(amount: 1e30)
        XCTAssertTrue(existing.displayText.hasSuffix("Stück Toilettenpapier"))
        XCTAssertFalse(existing.displayText.isEmpty)
    }
}
