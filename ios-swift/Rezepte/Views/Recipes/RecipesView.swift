import Foundation
import SwiftUI

struct RecipesView: View {
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var offline: OfflineRecipeStore
    @Environment(\.recipeTheme) private var theme
    @StateObject private var deletion = RecipeDeletionCoordinator()
    @State private var deleteTarget: RecipeDeletionTarget?
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
    @State private var showCookbooks = false
    @State private var showDiscovery = false
    @State private var showInbox = false
    @State private var showSavedSearches = false
    @State private var showOfflineLibrary = false
    @State private var showTrash = false

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
                if deletion.isDeleting {
                    ProgressView("Rezept wird gelöscht …")
                        .font(.subheadline)
                        .padding(.vertical, 8)
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
                                let target = RecipeDeletionTarget(recipe)
                                NavigationLink(value: recipe.id) {
                                    RecipeRow(recipe: recipe)
                                }
                                .listRowBackground(Color.clear)
                                .listRowSeparator(.hidden)
                                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                    if deletion.canDelete(target, session: session) {
                                        // Tint instead of a destructive role: the row must stay
                                        // visible until the user confirms and the server replies.
                                        Button { deleteTarget = target } label: {
                                            Label("Löschen", systemImage: "trash")
                                        }
                                        .tint(.red)
                                        .accessibilityLabel("„\(recipe.name)“ löschen")
                                    }
                                }
                                .contextMenu {
                                    if deletion.canDelete(target, session: session) {
                                        Button(role: .destructive) { deleteTarget = target } label: {
                                            Label("Rezept löschen", systemImage: "trash")
                                        }
                                    }
                                }
                                .disabled(deletion.isDeleting)
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
                ToolbarItem(placement: .topBarLeading) {
                    Menu {
                        if !session.readOnly {
                            Button("Gespeicherte Suchen", systemImage: "bookmark") { showSavedSearches = true }
                            Button("Offline-Rezepte", systemImage: "arrow.down.circle") { showOfflineLibrary = true }
                            Button("Was kann ich kochen?", systemImage: "carrot") { showDiscovery = true }
                            Button("Meine Kochbücher", systemImage: "books.vertical") { showCookbooks = true }
                            if session.role != .guest {
                                Button("Papierkorb", systemImage: "trash") { showTrash = true }
                                    .disabled(session.isOfflineSession)
                            }
                        }
                        if session.canImport {
                            Button("Eingang & Import", systemImage: "tray.and.arrow.down") { showInbox = true }
                        }
                    } label: { Label("Sammlung & Import", systemImage: "plus.circle") }
                }
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
            .sheet(isPresented: $showCookbooks) { CookbooksView() }
            .sheet(isPresented: $showOfflineLibrary) { OfflineLibraryView() }
            .sheet(isPresented: $showTrash) { RecipeTrashView().id(session.identity) }
            .sheet(isPresented: $showSavedSearches) {
                SavedRecipeSearchesView(query: search, filters: filters) { saved in
                    search = saved.query
                    filters = saved.filters
                    Task { await load(); await loadFacets() }
                }
            }
            .sheet(isPresented: $showDiscovery) { IngredientDiscoveryView() }
            .sheet(isPresented: $showInbox) {
                InboxView().safeAreaInset(edge: .top) {
                    HStack { Spacer(); Button("Schließen") { showInbox = false }.padding() }
                        .background(theme.background)
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
            .confirmationDialog(
                deleteTarget.map { "„\($0.name)“ löschen?" } ?? "Rezept löschen?",
                isPresented: Binding(get: { deleteTarget != nil }, set: { if !$0 { deleteTarget = nil } }),
                titleVisibility: .visible
            ) {
                if let target = deleteTarget {
                    Button("„\(target.name)“ löschen", role: .destructive) {
                        deleteTarget = nil
                        Task { _ = await deletion.delete(target, session: session, offline: offline) }
                    }
                    .disabled(deletion.isDeleting || !deletion.canDelete(target, session: session))
                }
                Button("Abbrechen", role: .cancel) { deleteTarget = nil }
            } message: {
                if let target = deleteTarget { Text(target.confirmationMessage) }
            }
            .onChange(of: session.identity) { _, _ in deleteTarget = nil; showTrash = false }
            .task {
                await load()
                await loadFacets()
            }
            .onChange(of: search) { _, newValue in
                if newValue.isEmpty { Task { await load() } }
            }
            .onReceive(NotificationCenter.default.publisher(for: .recipeDeleted)) { notification in
                guard let id = notification.object as? Int else { return }
                // DELETE is already acknowledged. Keep the row removed even
                // if the follow-up list request fails or an older page arrives.
                requestID = UUID()
                isLoading = false; isLoadingMore = false
                let previousCount = recipes.count
                recipes.removeAll { $0.id == id }
                total = max(0, total - (previousCount - recipes.count))
                if deleteTarget?.id == id { deleteTarget = nil }
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
                    .foregroundStyle(recipe.readiness == .ready ? theme.success : theme.warning)
                    .fixedSize(horizontal: false, vertical: true)
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
