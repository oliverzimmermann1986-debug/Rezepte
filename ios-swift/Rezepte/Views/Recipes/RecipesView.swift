import Foundation
import SwiftUI

struct RecipesView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var recipes: [RecipeSummary] = []
    @State private var total = 0
    @State private var search = ""
    @State private var filters = RecipeFilters()
    @State private var facets = RecipeFacets.empty
    @State private var showFilters = false
    @State private var isLoading = true
    @State private var isLoadingMore = false
    @State private var errorMessage: String?
    @State private var requestID = UUID()

    private var hasMore: Bool { recipes.count < total }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if !session.readOnly, session.supports("household-libraries-v1") {
                    Picker("Sammlung", selection: $filters.library) {
                        ForEach(RecipeLibrary.allCases) { library in Text(library.title).tag(library) }
                    }
                    .pickerStyle(.segmented)
                    .padding(.horizontal)
                    .padding(.bottom, 8)
                    .onChange(of: filters.library) { _, _ in
                        Task { await load(); await loadFacets() }
                    }
                }
                Group {
                    if isLoading && recipes.isEmpty {
                        ProgressView("Rezepte werden geladen …")
                    } else if let errorMessage, recipes.isEmpty {
                        ErrorState(message: errorMessage) {
                            Task { await load() }
                        }
                    } else if recipes.isEmpty {
                        EmptyState(
                            icon: filters.activeCount > 0 ? "line.3.horizontal.decrease.circle" : "fork.knife",
                            title: filters.activeCount > 0 ? "Keine Treffer" : "Keine Rezepte",
                            message: filters.activeCount > 0
                                ? "Passe die aktiven Filter an."
                                : session.readOnly
                                    ? "Für den Gastzugang sind derzeit keine Rezepte verfügbar."
                                    : session.canImport
                                        ? "Passe die Suche an oder importiere ein Rezept."
                                        : "Passe die Suche an oder speichere ein globales Rezept in deiner Sammlung."
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
            }
            .background(theme.background)
            .navigationTitle("Archiv")
            .navigationDestination(for: Int.self) { id in
                RecipeDetailView(recipeID: id)
            }
            .searchable(text: $search, prompt: "Rezepte durchsuchen")
            .onSubmit(of: .search) { Task { await load() } }
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showFilters = true
                    } label: {
                        Label(
                            filters.activeCount > 0
                                ? "Filter, \(filters.activeCount) aktiv"
                                : "Filter",
                            systemImage: filters.activeCount > 0
                                ? "line.3.horizontal.decrease.circle.fill"
                                : "line.3.horizontal.decrease.circle"
                        )
                    }
                    .accessibilityValue("\(filters.activeCount) aktiv")
                }
            }
            .sheet(isPresented: $showFilters) {
                RecipeFiltersView(
                    filters: filters,
                    facets: facets,
                    initialMatchCount: total,
                    loadMatchCount: { candidate in
                        try await session.api.recipeCount(search: search, filters: candidate)
                    },
                    loadFacets: { candidate in
                        try await session.api.recipeFacets(search: search, filters: candidate)
                    }
                ) { updated in
                    filters = updated
                    showFilters = false
                    Task {
                        await load()
                        await loadFacets()
                    }
                }
            }
            .task {
                await load()
                await loadFacets()
            }
            .onChange(of: search) { _, newValue in
                if newValue.isEmpty { Task { await load() } }
            }
            .onReceive(NotificationCenter.default.publisher(for: .recipesChanged)) { _ in
                Task {
                    await load()
                    await loadFacets()
                }
            }
        }
    }

    private func load() async {
        let currentRequest = UUID()
        requestID = currentRequest
        isLoading = true
        isLoadingMore = false
        errorMessage = nil
        defer { if requestID == currentRequest { isLoading = false } }
        do {
            let response = try await session.api.recipes(
                search: search,
                filters: filters
            )
            guard requestID == currentRequest else { return }
            recipes = response.items
            total = response.total
        } catch {
            guard requestID == currentRequest else { return }
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    /// Nächste Seite anhängen. Der Server kennt den Filter, deshalb ist
    /// `response.total` die vollständige Trefferzahl und nicht die der Seite.
    private func loadMore() async {
        guard !isLoading, !isLoadingMore, hasMore else { return }
        let currentRequest = requestID
        isLoadingMore = true
        defer { if requestID == currentRequest { isLoadingMore = false } }
        do {
            let response = try await session.api.recipes(
                search: search,
                filters: filters,
                offset: recipes.count
            )
            guard requestID == currentRequest else { return }
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
            guard requestID == currentRequest else { return }
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func loadFacets() async {
        let expectedFilters = filters
        let expectedSearch = search
        do {
            let result = try await session.api.recipeFacets(search: expectedSearch, filters: expectedFilters)
            guard filters == expectedFilters, search == expectedSearch else { return }
            facets = result
        } catch {
            // Die Liste bleibt nutzbar, selbst wenn nur die Filtervorschläge
            // vorübergehend nicht geladen werden können.
        }
    }
}

private struct RecipeRow: View {
    let recipe: RecipeSummary
    @Environment(\.recipeTheme) private var theme

    var body: some View {
        HStack(spacing: 14) {
            AuthenticatedImage(
                recipeID: recipe.id,
                height: 92,
                cacheVersion: recipe.thumbnailVersion
            )
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

                if let source = sourceLabel(recipe.url) {
                    Label(source, systemImage: "link")
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(theme.muted)
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

                    Label(recipe.readiness.title, systemImage: recipe.readiness.symbol)
                        .font(.caption.bold())
                        .foregroundStyle(recipe.readiness == .ready ? theme.success
                                         : recipe.readiness == .failed ? theme.danger : theme.warning)
            }
        }
        .padding(.vertical, 7)
        .contentShape(Rectangle())
    }

    private func sourceLabel(_ rawURL: String?) -> String? {
        guard let rawURL, let url = URL(string: rawURL), let host = url.host?.lowercased() else {
            return nil
        }
        if host.contains("pinterest") || host == "pin.it" { return "Pinterest" }
        if host.contains("youtube") || host == "youtu.be" { return "YouTube" }
        if host.contains("tiktok") { return "TikTok" }
        if host.contains("instagram") { return "Instagram" }
        return host.replacingOccurrences(of: "www.", with: "")
    }
}
