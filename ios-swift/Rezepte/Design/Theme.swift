import SwiftUI

enum AppTheme {
    static let butter = Color(red: 0.96, green: 0.78, blue: 0.31)
    static let butterSoft = Color(red: 1.00, green: 0.94, blue: 0.72)
    static let cream = Color(red: 1.00, green: 0.98, blue: 0.94)
    static let cocoa = Color(red: 0.26, green: 0.20, blue: 0.15)
    static let warning = Color(red: 0.72, green: 0.32, blue: 0.12)
    static let cornerRadius: CGFloat = 18
}

struct CardSurface: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(16)
            .background(.background, in: RoundedRectangle(cornerRadius: AppTheme.cornerRadius))
            .overlay {
                RoundedRectangle(cornerRadius: AppTheme.cornerRadius)
                    .stroke(.primary.opacity(0.08))
            }
    }
}

extension View {
    func cardSurface() -> some View {
        modifier(CardSurface())
    }
}

