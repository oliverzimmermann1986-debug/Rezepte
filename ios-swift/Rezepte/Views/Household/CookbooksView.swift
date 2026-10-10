import SwiftUI

@MainActor
struct CookbooksView: View {
    var recipeID: Int? = nil
    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @StateObject private var state = HouseholdFeatureState()
    @State private var books: [HouseholdCookbook] = []
    @State private var selected: HouseholdCookbook?
    @State private var recipes: [HouseholdRecipe] = []
    @State private var name = ""
    @State private var editing = false
    @State private var ready = false
    @State private var deleteTarget: HouseholdCookbook?

    var body: some View {
        NavigationStack {
            List {
                HouseholdFeedback(state: state)
                if !session.readOnly, session.role != .guest, state.identity == session.identity {
                    if ready {
                        if let selected {
                            Section {
                                Button("Alle Kochbücher", systemImage: "chevron.left") {
                                    self.selected = nil; recipes = []; editing = false; name = ""
                                }
                                Button("Umbenennen", systemImage: "pencil") { name = selected.name; editing = true }
                                Button("Kochbuch löschen", systemImage: "trash", role: .destructive) { deleteTarget = selected }
                            }
                        }
                        if selected == nil || editing {
                            Section(editing ? "Neuer Name" : "Neues Kochbuch") {
                                TextField("Zum Beispiel: Für Gäste", text: $name)
                                Button(editing ? "Name speichern" : "Kochbuch erstellen") { Task { await save() } }
                                    .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || name.count > 80)
                                if editing { Button("Abbrechen") { editing = false; name = "" } }
                            }
                        }
                        if selected == nil {
                            Section(recipeID == nil ? "Sammlungen deines Haushalts" : "Dieses Rezept sammeln") {
                                if books.isEmpty { Text("Lege ein Kochbuch an und sammle darin deine Rezepte.").foregroundStyle(.secondary) }
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
                state.reset(session); books = []; recipes = []; selected = nil; name = ""; editing = false; ready = false
                await load()
            }
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
        do {
            let result = try await session.api.householdCookbooks(recipeID: recipeID)
            guard state.current(token, session) else { return }
            books = result; selected = nil; recipes = []; editing = false; name = ""; ready = true
        } catch {
            guard state.current(token, session) else { return }
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
        do {
            let result = try await session.api.cookbookRecipes(id: book.id)
            guard state.current(token, session) else { return }
            recipes = result; selected = book
        } catch { state.failed(error, token: token, session: session) }
    }

    private func remove(_ book: HouseholdCookbook) async {
        deleteTarget = nil
        await mutate {
            try await session.api.deleteCookbook(id: book.id)
        }
    }
}
