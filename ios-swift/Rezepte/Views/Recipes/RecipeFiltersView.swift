import SwiftUI

struct RecipeFiltersView: View {
    @Environment(\.dismiss) private var dismiss

    let facets: RecipeFacets
    let onApply: (RecipeFilters) -> Void

    @State private var draft: RecipeFilters
    @State private var ingredientSearch = ""

    init(
        filters: RecipeFilters,
        facets: RecipeFacets,
        onApply: @escaping (RecipeFilters) -> Void
    ) {
        self.facets = facets
        self.onApply = onApply
        _draft = State(initialValue: filters)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Schnellfilter") {
                    Toggle("Nur Favoriten", isOn: $draft.favoriteOnly)
                    Toggle("Manuell zu pflegen", isOn: $draft.manualOnly)
                    Picker("Mindestbewertung", selection: $draft.minRating) {
                        Text("Alle").tag(0)
                        ForEach(1...5, id: \.self) { rating in
                            Text(String(repeating: "★", count: rating)).tag(rating)
                        }
                    }
                }

                Section("Einordnung") {
                    Picker("Typ", selection: $draft.type) {
                        Text("Alle Typen").tag("")
                        ForEach(facets.types, id: \.self) { value in
                            Text(value).tag(value)
                        }
                    }
                    Picker("Kategorie", selection: $draft.category) {
                        Text("Alle Kategorien").tag("")
                        ForEach(facets.categories, id: \.self) { value in
                            Text(value).tag(value)
                        }
                    }
                }

                if !facets.tags.isEmpty {
                    Section("Tags") {
                        ForEach(facets.tags) { tag in
                            Toggle(
                                "\(tag.name) (\(tag.n))",
                                isOn: tagBinding(tag.id)
                            )
                        }
                    }
                }

                Section {
                    TextField("Zutat suchen", text: $ingredientSearch)
                        .textInputAutocapitalization(.never)

                    ForEach(filteredIngredients) { ingredient in
                        Picker(
                            "\(ingredient.displayName) (\(ingredient.n))",
                            selection: ingredientBinding(ingredient.canonicalName)
                        ) {
                            Text("Egal").tag(IngredientChoice.any)
                            Text("Mit").tag(IngredientChoice.include)
                            Text("Ohne").tag(IngredientChoice.exclude)
                        }
                        .pickerStyle(.menu)
                    }
                } header: {
                    Text("Zutaten")
                } footer: {
                    Text("„Mit“ verlangt die Zutat, „Ohne“ schließt sie aus.")
                }
            }
            .navigationTitle(
                draft.activeCount == 0
                    ? "Rezepte filtern"
                    : "\(draft.activeCount) Filter aktiv"
            )
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Abbrechen") { dismiss() }
                }
                ToolbarItem(placement: .topBarLeading) {
                    if draft.activeCount > 0 {
                        Button("Zurücksetzen") { draft = RecipeFilters() }
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Anwenden") { onApply(draft) }
                        .fontWeight(.semibold)
                }
            }
        }
    }

    private var filteredIngredients: [IngredientFacet] {
        let query = ingredientSearch.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return facets.ingredients }
        return facets.ingredients.filter {
            $0.displayName.localizedCaseInsensitiveContains(query)
                || $0.canonicalName.localizedCaseInsensitiveContains(query)
        }
    }

    private func tagBinding(_ id: Int) -> Binding<Bool> {
        Binding(
            get: { draft.tagIDs.contains(id) },
            set: { selected in
                if selected { draft.tagIDs.insert(id) }
                else { draft.tagIDs.remove(id) }
            }
        )
    }

    private func ingredientBinding(_ name: String) -> Binding<IngredientChoice> {
        Binding(
            get: {
                if draft.includedIngredients.contains(name) { return .include }
                if draft.excludedIngredients.contains(name) { return .exclude }
                return .any
            },
            set: { choice in
                draft.includedIngredients.remove(name)
                draft.excludedIngredients.remove(name)
                if choice == .include { draft.includedIngredients.insert(name) }
                if choice == .exclude { draft.excludedIngredients.insert(name) }
            }
        )
    }
}

private enum IngredientChoice: String, Hashable {
    case any
    case include
    case exclude
}
