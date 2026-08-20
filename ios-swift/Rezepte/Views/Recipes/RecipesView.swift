import SwiftUI

struct RecipesView: View {
    @EnvironmentObject private var session: SessionStore
    @State private var recipes: [RecipeSummary] = []
    @State private var total = 0
    @State private var search = ""
    @State private var manualOnly = false
    @State private var isLoading = true
    @State private var isLoadingMore = false
    @State private var errorMessage: String?

    private var hasMore: Bool { recipes.count < total }

    var body: some View {
        NavigationStack {
            Group {
                if isLoading && recipes.isEmpty {
                    ProgressView("Rezepte werden geladen …")
                } else if let errorMessage, recipes.isEmpty {
                    ErrorState(message: errorMessage) {
                        Task { await load() }
                    }
                } else if recipes.isEmpty {
                    EmptyState(
                        icon: manualOnly ? "checkmark.seal" : "fork.knife",
                        title: manualOnly ? "Alles vollständig" : "Keine Rezepte",
                        message: manualOnly
                            ? "Aktuell muss kein Rezept manuell gepflegt werden."
                            : "Passe die Suche an oder importiere ein Rezept."
                    )
                } else {
                    List {
                        ForEach(recipes) { recipe in
                            NavigationLink(value: recipe.id) {
                                RecipeRow(recipe: recipe)
                            }
                            .listRowBackground(Color.clear)
                            .listRowSeparator(.hidden)
                        }

                        if hasMore {
                            HStack {
                                Spacer()
                                ProgressView()
                                Spacer()
                            }
                            .padding(.vertical, 12)
                            .listRowBackground(Color.clear)
                            .listRowSeparator(.hidden)
                            .accessibilityLabel("Weitere Rezepte werden geladen")
                            .task { await loadMore() }
                        }
                    }
                    .listStyle(.plain)
                    .refreshable { await load() }
                }
            }
            .background(AppTheme.cream)
            .navigationTitle("Rezepte")
            .navigationDestination(for: Int.self) { id in
                RecipeDetailView(recipeID: id)
            }
            .searchable(text: $search, prompt: "Rezepte durchsuchen")
            .onSubmit(of: .search) { Task { await load() } }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        manualOnly.toggle()
                        Task { await load() }
                    } label: {
                        Label(
                            "Manuell pflegen",
                            systemImage: manualOnly
                                ? "exclamationmark.triangle.fill"
                                : "exclamationmark.triangle"
                        )
                    }
                    .accessibilityValue(manualOnly ? "Filter aktiv" : "Filter inaktiv")
                }
            }
            .task { await load() }
            .onChange(of: search) { _, newValue in
                if newValue.isEmpty { Task { await load() } }
            }
        }
    }

    private func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let response = try await session.api.recipes(
                search: search,
                manualOnly: manualOnly
            )
            recipes = response.items
            total = response.total
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    /// Nächste Seite anhängen. Der Server kennt den Filter, deshalb ist
    /// `response.total` die vollständige Trefferzahl und nicht die der Seite.
    private func loadMore() async {
        guard !isLoadingMore, hasMore else { return }
        isLoadingMore = true
        defer { isLoadingMore = false }
        do {
            let response = try await session.api.recipes(
                search: search,
                manualOnly: manualOnly,
                offset: recipes.count
            )
            total = response.total
            let bekannt = Set(recipes.map(\.id))
            let neue = response.items.filter { !bekannt.contains($0.id) }
            recipes.append(contentsOf: neue)
            if neue.isEmpty {
                // Server liefert nichts mehr (z.B. zwischenzeitlich gelöscht):
                // Ladezeile zurückziehen, statt endlos weiterzufragen.
                total = recipes.count
            }
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }
}

private struct RecipeRow: View {
    let recipe: RecipeSummary

    var body: some View {
        HStack(spacing: 14) {
            AuthenticatedImage(recipeID: recipe.id, height: 92)
                .frame(width: 104)
                .clipShape(RoundedRectangle(cornerRadius: 14))

            VStack(alignment: .leading, spacing: 7) {
                Text(recipe.name)
                    .font(.headline)
                    .foregroundStyle(.primary)
                    .lineLimit(2)

                if let category = recipe.category, !category.isEmpty {
                    Text(category)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                HStack(spacing: 10) {
                    Label("\(recipe.ingredientsCount)", systemImage: "carrot")
                    Label("\(recipe.stepsCount)", systemImage: "list.number")
                    if recipe.isFavorite {
                        Image(systemName: "heart.fill")
                            .foregroundStyle(.red)
                            .accessibilityLabel("Favorit")
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)

                if recipe.needsManualCare {
                    Label("Manuell pflegen", systemImage: "exclamationmark.triangle.fill")
                        .font(.caption.bold())
                        .foregroundStyle(AppTheme.warning)
                }
            }
        }
        .padding(.vertical, 7)
        .contentShape(Rectangle())
    }
}

