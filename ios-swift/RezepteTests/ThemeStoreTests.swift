import Foundation
import SwiftUI
import UIKit
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

    func testPrimaryButtonTextMeetsAAInEveryThemeAndAppearance() {
        for choice in ThemeChoice.allCases {
            let theme = choice.theme
            for style in [UIUserInterfaceStyle.light, .dark] {
                let traits = UITraitCollection(userInterfaceStyle: style)
                let foreground = components(of: theme.accentForeground, traits: traits)
                let background = components(of: theme.accent, traits: traits)
                let foregroundLuminance = luminance(foreground)
                let backgroundLuminance = luminance(background)
                let ratio = (max(foregroundLuminance, backgroundLuminance) + 0.05)
                    / (min(foregroundLuminance, backgroundLuminance) + 0.05)

                XCTAssertEqual(foreground[3], 1, accuracy: 0.000001)
                XCTAssertEqual(background[3], 1, accuracy: 0.000001)
                XCTAssertGreaterThanOrEqual(
                    ratio, 4.5,
                    "\(choice.title) / \(style == .dark ? "dark" : "light"): \(ratio):1"
                )
            }
        }
    }

    func testAccentAndItsForegroundStayConstantAcrossAppearances() {
        for choice in ThemeChoice.allCases {
            for color in [choice.theme.accent, choice.theme.accentForeground] {
                let light = components(of: color, traits: UITraitCollection(userInterfaceStyle: .light))
                let dark = components(of: color, traits: UITraitCollection(userInterfaceStyle: .dark))
                for channel in 0..<4 {
                    XCTAssertEqual(light[channel], dark[channel], accuracy: 0.000001, choice.title)
                }
            }
        }
    }

    private func components(of color: Color, traits: UITraitCollection) -> [Double] {
        let resolved = UIColor(color).resolvedColor(with: traits)
        var red: CGFloat = 0
        var green: CGFloat = 0
        var blue: CGFloat = 0
        var alpha: CGFloat = 0
        XCTAssertTrue(resolved.getRed(&red, green: &green, blue: &blue, alpha: &alpha))
        return [red, green, blue, alpha].map(Double.init)
    }

    private func luminance(_ rgba: [Double]) -> Double {
        let linear = rgba.prefix(3).map { channel in
            channel <= 0.04045 ? channel / 12.92 : pow((channel + 0.055) / 1.055, 2.4)
        }
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]
    }
}
