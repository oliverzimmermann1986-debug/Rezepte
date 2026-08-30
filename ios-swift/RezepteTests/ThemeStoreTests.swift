import Foundation
import XCTest
@testable import Rezepte

@MainActor
final class ThemeStoreTests: XCTestCase {
    func testPlumIsTheDefaultTheme() throws {
        let suiteName = "ThemeStoreDefaultTests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }

        XCTAssertEqual(ThemeStore(defaults: defaults).selection, .plum)
    }

    func testThemeAndAppearancePersistPerDevice() throws {
        let suiteName = "ThemeStoreTests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }

        let first = ThemeStore(defaults: defaults)
        first.selection = .plum
        first.appearance = .dark

        let restored = ThemeStore(defaults: defaults)
        XCTAssertEqual(restored.selection, .plum)
        XCTAssertEqual(restored.appearance, .dark)
    }
}
