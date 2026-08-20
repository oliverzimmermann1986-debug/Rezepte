import SwiftUI

struct RecipePickerView: View {
    let day: MealDay
    let onSaved: () async -> Void

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @State private var recipes: [RecipeSummary] = []
    @State private var search = ""
    @State private var servings = 2
    @State private var isLoading = true

    var body: some View {
        NavigationStack {
            Group {
                if isLoading {
                    ProgressView("Rezepte werden geladen …")
                } else {
                    List(filteredRecipes) { recipe in
                        Button {
                            Task { await add(recipe) }
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(recipe.name)
                                        .foregroundStyle(.primary)
                                    if recipe.needsManualCare {
                                        Label("Unvollständig", systemImage: "exclamationmark.triangle.fill")
                                            .font(.caption)
                                            .foregroundStyle(AppTheme.warning)
                                    }
                                }
                                Spacer()
                                Image(systemName: "plus.circle.fill")
                            }
                        }
                    }
                }
            }
            .navigationTitle(day.label)
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $search, prompt: "Rezept suchen")
            .safeAreaInset(edge: .bottom) {
                HStack {
                    Text("Portionen")
                    Spacer()
                    Stepper("\(servings)", value: $servings, in: 1...24)
                        .fixedSize()
                }
                .padding()
                .background(.bar)
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Schließen") { dismiss() }
                }
            }
            .task { await load() }
        }
    }

    private var filteredRecipes: [RecipeSummary] {
        guard !search.isEmpty else { return recipes }
        return recipes.filter { $0.name.localizedCaseInsensitiveContains(search) }
    }

    private func load() async {
        defer { isLoading = false }
        do {
            recipes = try await session.api.recipes().items
        } catch {
            session.handle(error)
        }
    }

    private func add(_ recipe: RecipeSummary) async {
        do {
            _ = try await session.api.addMeal(
                date: day.date,
                recipeID: recipe.id,
                servings: servings
            )
            await onSaved()
            dismiss()
        } catch {
            session.handle(error)
        }
    }
}

