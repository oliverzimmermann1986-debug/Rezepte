import SwiftUI

@MainActor
struct CookbooksView: View {
    var recipeID: Int? = nil
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var offline: OfflineRecipeStore
    @Environment(\.dismiss) private var dismiss
    @StateObject private var state = HouseholdFeatureState()
    @State private var books: [HouseholdCookbook] = []
    @State private var selected: HouseholdCookbook?
    @State private var recipes: [HouseholdRecipe] = []
    @State private var name = ""
    @State private var editing = false
    @State private var ready = false
    @State private var deleteTarget: HouseholdCookbook?
    @State private var pdf: CookbookPDFDocument?
    @State private var cachedOnly = false

    var body: some View {
        NavigationStack {
            List {
                HouseholdFeedback(state: state)
                if !session.readOnly, session.role != .guest, state.identity == session.identity {
                    if ready {
                        if let selected {
                            Section {
                                Button("Alle Kochbücher", systemImage: "chevron.left") {
                                    self.selected = nil; recipes = []; editing = false; name = ""; pdf = nil
                                }
                                if !cachedOnly {
                                    Button("Umbenennen", systemImage: "pencil") { name = selected.name; editing = true }
                                    Button("Kochbuch löschen", systemImage: "trash", role: .destructive) { deleteTarget = selected }
                                }
                            }
                            Section("Mitnehmen") {
                                if !cachedOnly, session.supports("cookbook-pdf-v1") {
                                    Button("Kochbuch als PDF erstellen", systemImage: "doc.richtext") {
                                        Task { await exportPDF(selected) }
                                    }
                                    .disabled(recipes.isEmpty || recipes.count > 100)
                                    if recipes.count > 100 { Text("Für den PDF-Export bitte in Kochbücher mit höchstens 100 Rezepten aufteilen.").font(.caption).foregroundStyle(.secondary) }
                                }
                                if let pdf {
                                    ShareLink(item: pdf, preview: SharePreview(selected.name)) {
                                        Label("PDF teilen oder sichern", systemImage: "square.and.arrow.up")
                                    }
                                }
                                if !cachedOnly {
                                    Button(offline.isCookbookSaved(id: selected.id, session: session) ? "Offline-Kopie aktualisieren" : "Offline speichern",
                                           systemImage: "arrow.down.circle") {
                                        Task { await saveOffline(selected) }
                                    }.disabled(recipes.isEmpty || offline.isDownloading)
                                }
                                if offline.isCookbookSaved(id: selected.id, session: session) {
                                    Button("Offline-Kopie entfernen", systemImage: "trash", role: .destructive) {
                                        removeOffline(selected)
                                    }.disabled(offline.isDownloading)
                                }
                                if offline.isDownloading, let progress = offline.progress { Text(progress).font(.caption).foregroundStyle(.secondary) }
                            }
                        }
                        if !cachedOnly && (selected == nil || editing) {
                            Section(editing ? "Neuer Name" : "Neues Kochbuch") {
                                TextField("Zum Beispiel: Für Gäste", text: $name)
                                Button(editing ? "Name speichern" : "Kochbuch erstellen") { Task { await save() } }
                                    .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || name.count > 80)
                                if editing { Button("Abbrechen") { editing = false; name = "" } }
                            }
                        }
                        if selected == nil {
                            Section(recipeID == nil ? "Sammlungen deines Haushalts" : "Dieses Rezept sammeln") {
                                if books.isEmpty { Text(cachedOnly ? "Noch keine Kochbücher offline gespeichert. Lade sie herunter, sobald du wieder verbunden bist." : "Lege ein Kochbuch an und sammle darin deine Rezepte.").foregroundStyle(.secondary) }
                                ForEach(books) { book in
                                    Button {
                                        Task { if let recipeID { await membership(book, recipeID: recipeID) } else { await open(book) } }
                                    } label: {
                                        HStack {
                                            VStack(alignment: .leading) {
                                                Text(book.name).foregroundStyle(.primary)
                                                Text("\(book.recipeCount) Rezepte").font(.caption).foregroundStyle(.secondary)
                                            }
                                            Spacer()
                                            Image(systemName: recipeID == nil ? "chevron.right" : book.containsRecipe ? "checkmark.circle.fill" : "plus.circle")
                                        }.frame(minHeight: 44)
                                    }
                                    .disabled(cachedOnly && recipeID != nil)
                                    .accessibilityLabel(recipeID == nil ? book.name : "\(book.name), \(book.containsRecipe ? "Rezept enthalten" : "Rezept hinzufügen")")
                                }
                            }
                        } else {
                            Section("Rezepte") {
                                if recipes.isEmpty { Text("Noch keine Rezepte. Wähle auf einer Rezeptseite „In Kochbüchern sammeln“.").foregroundStyle(.secondary) }
                                ForEach(recipes) { recipe in
                                    NavigationLink(recipe.name) { RecipeDetailView(recipeID: recipe.id) }
                                }
                            }
                        }
                    } else if !state.busy {
                        Button("Kochbücher erneut laden") { Task { await load() } }
                    }
                }
            }
            .disabled(state.busy)
            .navigationTitle(selected?.name ?? "Kochbücher")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Fertig") { dismiss() }.disabled(state.busy) } }
            .interactiveDismissDisabled(state.busy)
            .refreshable { await load() }
            .task(id: session.identity) {
                state.reset(session); books = []; recipes = []; selected = nil; name = ""; editing = false; ready = false; pdf = nil; cachedOnly = false
                await offline.activate(session: session)
                await load()
            }
            .onDisappear { pdf = nil }
            .confirmationDialog("Kochbuch löschen?", isPresented: Binding(get: { deleteTarget != nil }, set: { if !$0 { deleteTarget = nil } })) {
                if let target = deleteTarget {
                    Button("„\(target.name)“ löschen", role: .destructive) { Task { await remove(target) } }
                }
                Button("Abbrechen", role: .cancel) { deleteTarget = nil }
            } message: { Text("Die Rezepte bleiben erhalten.") }
        }
    }

    private func load() async {
        guard let token = state.begin(session) else { return }
        defer { state.finish(token, session) }
        if recipeID == nil, session.isOfflineSession {
            books = offline.cachedCookbooks(session: session); selected = nil; recipes = []; editing = false
            ready = true; cachedOnly = true; pdf = nil
            state.notice = "Offline: Du siehst deine heruntergeladenen Kochbücher."
            return
        }
        do {
            let result = try await session.api.householdCookbooks(recipeID: recipeID)
            guard state.current(token, session) else { return }
            books = result; selected = nil; recipes = []; editing = false; name = ""; ready = true; cachedOnly = false; pdf = nil
        } catch {
            guard state.current(token, session) else { return }
            let cached = offline.cachedCookbooks(session: session)
            if recipeID == nil, OfflineRecipeStore.isConnectivityFailure(error), !cached.isEmpty {
                books = cached; selected = nil; recipes = []; editing = false; ready = true; cachedOnly = true; pdf = nil
                state.notice = "Offline: Deine heruntergeladenen Kochbücher sind verfügbar."
                return
            }
            ready = false; state.failed(error, token: token, session: session)
        }
    }

    private func mutate(_ operation: () async throws -> Void) async {
        guard ready, let token = state.begin(session) else { return }
        defer { state.finish(token, session) }
        do {
            try await operation()
            guard state.current(token, session) else { return }
            let result = try await session.api.householdCookbooks(recipeID: recipeID)
            guard state.current(token, session) else { return }
            books = result; name = ""; editing = false; state.notice = "Gespeichert."
            pdf = nil
            if let selected {
                self.selected = result.first { $0.id == selected.id }
                if self.selected == nil { recipes = [] }
            }
        } catch {
            guard state.current(token, session) else { return }
            ready = false // Read back authoritative state before another potentially duplicate write.
            state.failed(error, token: token, session: session)
        }
    }

    private func save() async {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed.count <= 80 else { return }
        let id = editing ? selected?.id : nil
        await mutate {
            _ = try await session.api.saveCookbook(name: trimmed, id: id)
        }
    }

    private func membership(_ book: HouseholdCookbook, recipeID: Int) async {
        await mutate { try await session.api.cookbookMembership(id: book.id, recipeID: recipeID, included: !book.containsRecipe) }
    }

    private func open(_ book: HouseholdCookbook) async {
        guard ready, let token = state.begin(session) else { return }
        defer { state.finish(token, session) }
        pdf = nil
        if cachedOnly {
            recipes = offline.cachedCookbookRecipes(id: book.id, session: session); selected = book
            return
        }
        do {
            let result = try await session.api.cookbookRecipes(id: book.id)
            guard state.current(token, session) else { return }
            recipes = result; selected = book
        } catch {
            guard state.current(token, session) else { return }
            if OfflineRecipeStore.isConnectivityFailure(error), offline.isCookbookSaved(id: book.id, session: session) {
                recipes = offline.cachedCookbookRecipes(id: book.id, session: session); selected = book; cachedOnly = true
                books = offline.cachedCookbooks(session: session)
                state.notice = "Offline: Du siehst die heruntergeladene Version."
            } else { state.failed(error, token: token, session: session) }
        }
    }

    private func exportPDF(_ book: HouseholdCookbook) async {
        guard session.supports("cookbook-pdf-v1"), let token = state.begin(session) else { return }
        defer { state.finish(token, session) }
        pdf = nil
        do {
            let data = try await session.api.cookbookPDF(id: book.id)
            guard state.current(token, session), selected?.id == book.id else { return }
            pdf = CookbookPDFDocument(data: data, name: book.name)
            state.notice = "Dein PDF ist bereit zum Teilen oder Sichern."
        } catch { state.failed(error, token: token, session: session) }
    }

    private func saveOffline(_ book: HouseholdCookbook) async {
        guard let token = state.begin(session) else { return }
        defer { state.finish(token, session) }
        do {
            try await offline.saveCookbook(book: book, session: session)
            guard state.current(token, session) else { return }
            state.notice = "Kochbuch offline gespeichert."
        } catch { state.failed(error, token: token, session: session) }
    }

    private func removeOffline(_ book: HouseholdCookbook) {
        do {
            try offline.removeCookbook(id: book.id, session: session)
            state.notice = "Offline-Kopie entfernt. Die Rezepte bleiben auf dem Server."
            state.error = offline.errorMessage
            if cachedOnly { selected = nil; recipes = []; books = offline.cachedCookbooks(session: session) }
        } catch { state.error = error.localizedDescription }
    }

    private func remove(_ book: HouseholdCookbook) async {
        deleteTarget = nil
        await mutate {
            try await session.api.deleteCookbook(id: book.id)
        }
    }
}
