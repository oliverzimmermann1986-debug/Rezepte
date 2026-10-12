import SwiftUI

struct ShoppingIngredientInfoView: View {
    let item: CartItem
    @Environment(\.dismiss) private var dismiss
    @Environment(\.recipeTheme) private var theme

    var body: some View {
        NavigationStack {
            List {
                Section("Auf der Einkaufsliste") {
                    Text(item.displayText).font(.headline)
                }
                Section {
                    if let sources = item.sourceContributions, !sources.isEmpty {
                        ForEach(Array(sources.enumerated()), id: \.offset) { _, source in
                            if let recipeID = source.recipeId {
                                NavigationLink {
                                    RecipeDetailView(recipeID: recipeID)
                                } label: {
                                    sourceLabel(source)
                                }
                                .accessibilityHint("Öffnet das Rezept, ohne den Einkauf zu ändern.")
                            } else {
                                sourceLabel(source)
                            }
                        }
                    } else {
                        Text("Für diesen Artikel wurde keine Rezeptzuordnung gespeichert.")
                            .foregroundStyle(theme.muted)
                    }
                } header: {
                    Text("Rezepte & Mengenanteile")
                } footer: {
                    Text("Die Mengenanteile stammen aus dem Hinzufügen zur Liste. Eine später von Hand geänderte Gesamtmenge kann davon abweichen.")
                }
            }
            .scrollContentBackground(.hidden)
            .background(theme.background)
            .navigationTitle(item.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Fertig") { dismiss() }
                }
            }
        }
    }

    private func sourceLabel(_ source: ShoppingContribution) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(source.recipeName)
            Text(source.quantityText).font(.caption).foregroundStyle(theme.muted)
        }
        .frame(minHeight: 44, alignment: .leading)
    }
}
