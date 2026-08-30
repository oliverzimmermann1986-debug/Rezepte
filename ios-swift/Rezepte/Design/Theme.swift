import SwiftUI
import UIKit

enum ThemeChoice: String, CaseIterable, Codable, Identifiable {
    case plum
    case butter
    case sage
    case tomato

    var id: String { rawValue }

    var title: String {
        switch self {
        case .butter: "Butter"
        case .sage: "Salbei"
        case .tomato: "Tomate"
        case .plum: "Pflaume"
        }
    }

    var subtitle: String {
        switch self {
        case .butter: "warm und vertraut"
        case .sage: "ruhig und natürlich"
        case .tomato: "kräftig und kulinarisch"
        case .plum: "warm und editorial"
        }
    }

    var theme: RecipeTheme {
        switch self {
        case .butter:
            RecipeTheme(
                accent: Color(red: 0.96, green: 0.78, blue: 0.31),
                accentPressed: Color(red: 0.86, green: 0.65, blue: 0.16),
                accentSoft: Color.dynamic(light: 0xFFF0B8, dark: 0x4A3C18),
                background: Color.dynamic(light: 0xFFF9EE, dark: 0x17130E),
                surface: Color.dynamic(light: 0xFFFDF8, dark: 0x211B14),
                ink: Color.dynamic(light: 0x433427, dark: 0xF6EBDD),
                muted: Color.dynamic(light: 0x77685B, dark: 0xBEB0A2),
                warning: Color.dynamic(light: 0x9A4D18, dark: 0xF0A465),
                outline: Color.dynamic(light: 0xE4D9CB, dark: 0x4A4037)
            )
        case .sage:
            RecipeTheme(
                accent: Color(red: 0.49, green: 0.64, blue: 0.46),
                accentPressed: Color(red: 0.34, green: 0.50, blue: 0.32),
                accentSoft: Color.dynamic(light: 0xDCE9D8, dark: 0x263A26),
                background: Color.dynamic(light: 0xF6F8F1, dark: 0x111711),
                surface: Color.dynamic(light: 0xFCFDF8, dark: 0x1B241B),
                ink: Color.dynamic(light: 0x29372A, dark: 0xEBF3E8),
                muted: Color.dynamic(light: 0x657066, dark: 0xAAB8A8),
                warning: Color.dynamic(light: 0x98511E, dark: 0xEBA66F),
                outline: Color.dynamic(light: 0xD5DFD1, dark: 0x3D4A3D)
            )
        case .tomato:
            RecipeTheme(
                accent: Color(red: 0.82, green: 0.31, blue: 0.22),
                accentPressed: Color(red: 0.68, green: 0.22, blue: 0.16),
                accentSoft: Color.dynamic(light: 0xF6DDD6, dark: 0x4A2520),
                background: Color.dynamic(light: 0xFFF7F2, dark: 0x1B1110),
                surface: Color.dynamic(light: 0xFFFCF9, dark: 0x281917),
                ink: Color.dynamic(light: 0x482A24, dark: 0xF8E9E4),
                muted: Color.dynamic(light: 0x78635D, dark: 0xC1AAA4),
                warning: Color.dynamic(light: 0x87520F, dark: 0xE9AA54),
                outline: Color.dynamic(light: 0xE7D3CD, dark: 0x50312C)
            )
        case .plum:
            RecipeTheme(
                accent: Color(red: 0.54, green: 0.34, blue: 0.50),
                accentPressed: Color(red: 0.42, green: 0.24, blue: 0.39),
                accentSoft: Color.dynamic(light: 0xEBDDEA, dark: 0x402A3E),
                background: Color.dynamic(light: 0xFFF9EE, dark: 0x181116),
                surface: Color.dynamic(light: 0xFFFDF8, dark: 0x251A22),
                ink: Color.dynamic(light: 0x3E2B39, dark: 0xF4E9F1),
                muted: Color.dynamic(light: 0x74636F, dark: 0xBBAAB6),
                warning: Color.dynamic(light: 0x985020, dark: 0xEDA56E),
                outline: Color.dynamic(light: 0xE0D2DC, dark: 0x51394B)
            )
        }
    }
}

enum AppearanceMode: String, CaseIterable, Identifiable {
    case system
    case light
    case dark

    var id: String { rawValue }

    var title: String {
        switch self {
        case .system: "System"
        case .light: "Hell"
        case .dark: "Dunkel"
        }
    }

    var colorScheme: ColorScheme? {
        switch self {
        case .system: nil
        case .light: .light
        case .dark: .dark
        }
    }
}

struct RecipeTheme: Equatable {
    let accent: Color
    let accentPressed: Color
    let accentSoft: Color
    let background: Color
    let surface: Color
    let ink: Color
    let muted: Color
    let warning: Color
    let outline: Color

    let success = Color.dynamic(light: 0x287A4B, dark: 0x63C88F)
    let danger = Color.dynamic(light: 0xA43D35, dark: 0xF08B83)
}

@MainActor
final class ThemeStore: ObservableObject {
    @Published var selection: ThemeChoice {
        didSet { defaults.set(selection.rawValue, forKey: Self.themeKey) }
    }

    @Published var appearance: AppearanceMode {
        didSet { defaults.set(appearance.rawValue, forKey: Self.appearanceKey) }
    }

    private static let themeKey = "appearance-theme-v1"
    private static let appearanceKey = "appearance-mode-v1"
    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        selection = ThemeChoice(rawValue: defaults.string(forKey: Self.themeKey) ?? "") ?? .plum
        appearance = AppearanceMode(rawValue: defaults.string(forKey: Self.appearanceKey) ?? "") ?? .system
    }

    var theme: RecipeTheme { selection.theme }
}

private struct RecipeThemeKey: EnvironmentKey {
    static let defaultValue = ThemeChoice.plum.theme
}

extension EnvironmentValues {
    var recipeTheme: RecipeTheme {
        get { self[RecipeThemeKey.self] }
        set { self[RecipeThemeKey.self] = newValue }
    }
}

enum AppTheme {
    static let cornerRadius: CGFloat = 18
}

struct CardSurface: ViewModifier {
    @Environment(\.recipeTheme) private var theme

    func body(content: Content) -> some View {
        content
            .padding(16)
            .background(theme.surface, in: RoundedRectangle(cornerRadius: AppTheme.cornerRadius))
            .overlay {
                RoundedRectangle(cornerRadius: AppTheme.cornerRadius)
                    .stroke(theme.outline.opacity(0.72))
            }
    }
}

extension View {
    func cardSurface() -> some View {
        modifier(CardSurface())
    }
}

private extension Color {
    static func dynamic(light: UInt32, dark: UInt32) -> Color {
        Color(uiColor: UIColor { traits in
            UIColor(rgb: traits.userInterfaceStyle == .dark ? dark : light)
        })
    }
}

private extension UIColor {
    convenience init(rgb: UInt32) {
        self.init(
            red: CGFloat((rgb >> 16) & 0xFF) / 255,
            green: CGFloat((rgb >> 8) & 0xFF) / 255,
            blue: CGFloat(rgb & 0xFF) / 255,
            alpha: 1
        )
    }
}
