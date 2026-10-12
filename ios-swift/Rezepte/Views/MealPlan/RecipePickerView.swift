import SwiftUI

struct RecipePickerView: View {
    let day: MealDay
    var replacing: MealEntry? = nil
    var weekStart: String? = nil
    var excluding: [Int] = []
    let onSaved: () async -> Void

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.recipeTheme) private var theme
    @State private var recipes: [RecipeSummary] = []
    @State private var search = ""
    @State private var servings = 2
    @State private var isLoading = true
    @State private var isSaving = false
    @State private var householdOnly = false
    @State private var suggested: MealSuggestion?
    @State private var errorMessage: String?
    @State private var loadedQuery: String?
    @State private var loadToken = UUID()
    @State private var initializedIdentity: UUID?
    private var queryKey: String { "\(session.identity)-\(search)-\(householdOnly)" }
    private var excludedIDs: Set<Int> { Set(excluding + day.items.map(\.recipeId)) }

    var body: some View {
        NavigationStack {
            Group {
                if isLoading {
                    ProgressView("Rezepte werden geladen …")
                } else {
                    List {
                        if session.supports("meal-plan-variety-v1") {
                            Section {
                                if session.supports("shared-recipes-household-filter-v1") {
                                    Toggle("Nur aus Haushalt", isOn: $householdOnly).disabled(isSaving)
                                }
                                Button("Zufälligen Vorschlag suchen", systemImage: "shuffle") { Task { await suggest() } }
                                    .disabled(isSaving)
                                if let suggested {
                                    NavigationLink(suggested.name) { RecipeDetailView(recipeID: suggested.id) }
                                    Button(replacing == nil ? "Vorschlag einplanen" : "Durch Vorschlag ersetzen") {
                                        Task { await save(recipeID: suggested.id) }
                                    }.disabled(isSaving)
                                }
                            }
                        }
                        if let errorMessage { Text(errorMessage).foregroundStyle(theme.danger) }
                        ForEach(filteredRecipes) { recipe in
                            Button {
                                Task { await save(recipeID: recipe.id) }
                            } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(recipe.name)
                                        .foregroundStyle(.primary)
                                    if recipe.readiness != .ready {
                                        Label(recipe.readiness.title, systemImage: recipe.readiness.symbol)
                                            .font(.caption)
                                            .foregroundStyle(theme.warning)
                                    }
                                }
                                Spacer()
                                Image(systemName: replacing == nil ? "plus.circle.fill" : "arrow.triangle.2.circlepath")
                            }
                            }
                            .disabled(isSaving)
                        }
                    }
                }
            }
            .navigationTitle(replacing == nil ? day.label : "Gericht austauschen")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $search, prompt: "Rezept suchen")
            .safeAreaInset(edge: .bottom) {
                HStack {
                    Text("Portionen")
                    Spacer()
                    Stepper("\(servings)", value: $servings, in: 1...24)
                        .fixedSize()
                        .disabled(isSaving)
                }
                .padding()
                .background(.bar)
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Schließen") { dismiss() }.disabled(isSaving)
                }
            }
            .task(id: queryKey) {
                if initializedIdentity != session.identity {
                    initializedIdentity = session.identity
                    servings = replacing?.plannedServings ?? session.defaultServings
                }
                await load()
            }
            .onChange(of: householdOnly) { _, _ in suggested = nil }
            .onChange(of: search) { _, _ in suggested = nil }
            .onChange(of: session.identity) { _, _ in
                suggested = nil; recipes = []; loadedQuery = nil; errorMessage = nil
            }
            .interactiveDismissDisabled(isSaving)
        }
    }

    private var filteredRecipes: [RecipeSummary] {
        recipes.filter { !Set(day.items.map(\.recipeId)).contains($0.id) }
    }

    private func load() async {
        let key = queryKey
        guard loadedQuery != key else { return }
        let token = UUID(); loadToken = token
        recipes = []
        isLoading = true
        defer { if loadToken == token { isLoading = false } }
        do {
            if !search.isEmpty { try await Task.sleep(for: .milliseconds(250)) }
            var filters = RecipeFilters()
            filters.library = householdOnly ? .mine : .all
            let result = try await session.api.recipes(search: search, filters: filters, limit: 200)
            guard queryKey == key, loadToken == token, !Task.isCancelled else { return }
            recipes = result.items; loadedQuery = key; errorMessage = nil
        } catch {
            guard queryKey == key, loadToken == token, !Task.isCancelled else { return }
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func suggest() async {
        guard !isSaving else { return }
        isSaving = true
        let identity = session.identity
        defer { isSaving = false }
        do {
            let result = try await session.api.constrainedMealSuggestions(count: 1, vegetarian: 0, maxMinutes: nil,
                excluding: Array(excludedIDs.union(suggested.map { [$0.id] } ?? [])), quotas: [], excludedIngredients: [],
                avoidRecentDays: 14, weekStart: weekStart ?? day.date, householdOnly: householdOnly,
                mealCourse: replacing?.mealCourse ?? "main", randomSeed: UUID())
            guard identity == session.identity else { return }
            if let item = result.items.first { suggested = item.recipe; errorMessage = nil }
            else { errorMessage = "Kein weiteres passendes Rezept gefunden. Suche nach einem Rezept oder ändere die Auswahl." }
        } catch {
            guard identity == session.identity else { return }
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func save(recipeID: Int) async {
        guard !isSaving, !session.readOnly, !Set(day.items.map(\.recipeId)).contains(recipeID) else { return }
        isSaving = true
        let identity = session.identity
        defer { isSaving = false }
        do {
            if let replacing {
                _ = try await session.api.replaceMeal(id: replacing.id, recipeID: recipeID,
                    expectedRecipeID: replacing.recipeId, servings: servings)
            } else {
                _ = try await session.api.addMeal(date: day.date, recipeID: recipeID, servings: servings)
            }
            guard identity == session.identity else { return }
            await onSaved()
            dismiss()
        } catch {
            guard identity == session.identity else { return }
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }
}
