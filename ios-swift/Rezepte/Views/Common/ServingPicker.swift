import SwiftUI

struct ServingPicker: View {
    @Binding var value: Int
    let original: Int
    var disabled = false

    @Environment(\.recipeTheme) private var theme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(spacing: 14) {
            Text("Originalrezept: \(original) \(original == 1 ? "Portion" : "Portionen")")
                .font(.caption)
                .foregroundStyle(theme.muted)

            HStack(spacing: 18) {
                Button {
                    update(to: value - 1)
                } label: {
                    Image(systemName: "minus")
                        .frame(width: 46, height: 46)
                }
                .buttonStyle(.bordered)
                .disabled(value <= 1 || disabled)
                .accessibilityLabel("Eine Portion weniger")

                VStack(spacing: 2) {
                    Text("\(value)")
                        .font(.largeTitle.bold().monospacedDigit())
                    Text(value == 1 ? "Portion" : "Portionen")
                        .font(.caption)
                        .foregroundStyle(theme.muted)
                }
                .frame(minWidth: 96)
                .accessibilityElement(children: .combine)
                .accessibilityLabel("Anzahl Portionen")
                .accessibilityValue("\(value)")

                Button {
                    update(to: value + 1)
                } label: {
                    Image(systemName: "plus")
                        .frame(width: 46, height: 46)
                }
                .buttonStyle(.bordered)
                .disabled(value >= 50 || disabled)
                .accessibilityLabel("Eine Portion mehr")
            }

            Text(value == original ? "Originalmenge" : "Zutaten werden passend skaliert")
                .font(.callout.weight(.semibold))
                .foregroundStyle(value == original ? theme.muted : theme.accentPressed)
        }
        .frame(maxWidth: .infinity)
        .cardSurface()
    }

    private func update(to newValue: Int) {
        let update = { value = min(50, max(1, newValue)) }
        if reduceMotion {
            update()
        } else {
            withAnimation(.snappy) { update() }
        }
    }
}
