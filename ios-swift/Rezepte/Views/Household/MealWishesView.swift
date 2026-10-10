import SwiftUI

@MainActor
struct MealWishesView: View {
    let week: MealWeek
    var onApplied: () async -> Void = {}
    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var state = HouseholdFeatureState()
    @State private var wishes: [MealWish] = []
    @State private var showPicker = false
    @State private var selectedID: Int?
    @State private var selectedDate = ""
    @State private var servings = 2
    @State private var pending: (id: Int, request: WishPlanRequest)?
    @State private var deleteTarget: MealWish?

    var body: some View {
        NavigationStack {
            List {
                HouseholdFeedback(state: state)
                if !session.readOnly, session.role != .guest, state.identity == session.identity {
                    Section {
                        Text("Woche ab \(week.weekStart). Jede Person hat eine Stimme pro Gericht.")
                            .foregroundStyle(.secondary)
                        Button("Gericht wünschen", systemImage: "plus") { showPicker = true }
                        Button("Wünsche aktualisieren", systemImage: "arrow.clockwise") { Task { await load() } }
                    }
                    if wishes.isEmpty, !state.busy, state.error == nil {
                        Text("Noch keine Wünsche. Sammelt gemeinsam Gerichte für diese Woche.")
                    }
                    ForEach(wishes.sorted { $0.votes == $1.votes ? $0.id < $1.id : $0.votes > $1.votes }) { wish in
                        Section {
                            NavigationLink(wish.recipeName) { RecipeDetailView(recipeID: wish.recipeId) }
                                .font(.headline)
                            Text("Gewünscht von \(wish.createdBy)").font(.caption).foregroundStyle(.secondary)
                            Button {
                                Task { await mutate { try await session.api.voteMealWish(id: wish.id, voted: !wish.myVote) } }
                            } label: {
                                Label("\(wish.votes) \(wish.votes == 1 ? "Stimme" : "Stimmen")", systemImage: wish.myVote ? "checkmark.circle.fill" : "plus.circle")
                                    .frame(minHeight: 44)
                            }
                            .accessibilityLabel(wish.myVote ? "Stimme für \(wish.recipeName) zurücknehmen" : "Für \(wish.recipeName) stimmen")
                            if let planned = wish.plannedFor {
                                Label("Im Plan: \(planned) · \(wish.plannedServings ?? 2) Portionen", systemImage: "calendar.badge.checkmark")
                                    .font(.subheadline)
                            } else if selectedID == wish.id {
                                Picker("Tag", selection: $selectedDate) {
                                    ForEach(week.days) { day in Text("\(day.label), \(day.date)").tag(day.date) }
                                }.disabled(pending != nil)
                                Stepper("\(servings) Portionen", value: $servings, in: 1...24).disabled(pending != nil)
                                if pending != nil { Text("Bestätigung ausstehend. Tag und Portionen bleiben für die Wiederholung erhalten.").font(.caption) }
                                Button(pending == nil ? "In den Wochenplan übernehmen" : "Übernahme erneut bestätigen") {
                                    Task { await apply(wish) }
                                }.buttonStyle(.borderedProminent)
                                if pending == nil { Button("Abbrechen") { selectedID = nil } }
                            } else {
                                Button("In den Wochenplan übernehmen") { selectedID = wish.id }
                                    .disabled(pending != nil)
                            }
                            if wish.canDelete, wish.plannedFor == nil {
                                Button("Wunsch entfernen", role: .destructive) { deleteTarget = wish }
                                    .disabled(pending != nil)
                            }
                        }
                    }
                }
            }
            .disabled(state.busy)
            .navigationTitle("Wochenwünsche")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Fertig") { dismiss() }.disabled(state.busy) } }
            .interactiveDismissDisabled(state.busy)
            .refreshable { await load() }
            .task(id: "\(session.identity)-\(week.weekStart)") {
                state.reset(session); wishes = []; pending = nil; selectedID = nil
                selectedDate = week.days.first(where: { $0.items.isEmpty })?.date ?? week.weekStart
                await load()
                while !Task.isCancelled {
                    do { try await Task.sleep(for: .seconds(20)) } catch { return }
                    if scenePhase == .active { await load() }
                }
            }
            .onChange(of: scenePhase) { _, value in if value == .active { Task { await load() } } }
            .sheet(isPresented: $showPicker) {
                WishRecipePicker(weekStart: week.weekStart, existing: Set(wishes.map(\.recipeId))) { await load() }
            }
            .confirmationDialog("Wunsch entfernen?", isPresented: Binding(get: { deleteTarget != nil }, set: { if !$0 { deleteTarget = nil } })) {
                if let target = deleteTarget {
                    Button(target.recipeName + " entfernen", role: .destructive) {
                        deleteTarget = nil
                        Task { await mutate { try await session.api.deleteMealWish(id: target.id) } }
                    }
                }
                Button("Abbrechen", role: .cancel) { deleteTarget = nil }
            }
        }
    }

    private func load() async {
        guard let token = state.begin(session) else { return }
        defer { state.finish(token, session) }
        do {
            let result = try await session.api.mealWishes(weekStart: week.weekStart)
            guard state.current(token, session) else { return }
            wishes = result
            if let pending, result.first(where: { $0.id == pending.id })?.plannedFor != nil || !result.contains(where: { $0.id == pending.id }) {
                self.pending = nil; selectedID = nil
                await onApplied()
            }
        } catch { state.failed(error, token: token, session: session) }
    }

    private func mutate(_ operation: () async throws -> Void) async {
        guard let token = state.begin(session) else { return }
        defer { state.finish(token, session) }
        do {
            try await operation()
            guard state.current(token, session) else { return }
            let result = try await session.api.mealWishes(weekStart: week.weekStart)
            guard state.current(token, session) else { return }
            wishes = result
        } catch { state.failed(error, token: token, session: session) }
    }

    private func apply(_ wish: MealWish) async {
        guard let token = state.begin(session) else { return }
        defer { state.finish(token, session) }
        let request = pending?.request ?? WishPlanRequest(plannedFor: selectedDate, plannedServings: servings)
        guard pending == nil || pending?.id == wish.id else { return }
        pending = (wish.id, request)
        var confirmed = false
        do {
            try await session.api.planMealWish(id: wish.id, request: request)
            guard state.current(token, session) else { return }
            confirmed = true; pending = nil; selectedID = nil
            await onApplied()
            guard state.current(token, session) else { return }
            let result = try await session.api.mealWishes(weekStart: week.weekStart)
            guard state.current(token, session) else { return }
            wishes = result; state.notice = "Das Gericht ist im Wochenplan."
        } catch {
            guard state.current(token, session) else { return }
            if case let APIError.server(status, _) = error, [400, 403, 404, 409, 422].contains(status) { pending = nil }
            state.failed(error, token: token, session: session)
            if confirmed { state.error = "Der Wunsch wurde übernommen. Bitte aktualisiere die Anzeige." }
        }
    }
}

@MainActor
private struct WishRecipePicker: View {
    let weekStart: String
    let existing: Set<Int>
    let onAdded: () async -> Void
    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @State private var recipes: [HouseholdRecipe] = []
    @State private var query = ""
    @State private var total = 0
    @State private var searching = false
    @State private var adding = false
    @State private var error: String?
    @State private var requestID = UUID()
    var body: some View {
        NavigationStack {
            List {
                if let error { Text(error).foregroundStyle(.red); Button("Erneut suchen") { Task { await search() } } }
                if searching { ProgressView() }
                ForEach(recipes) { recipe in
                    Button { Task { await add(recipe) } } label: {
                        HStack { Text(recipe.name); Spacer(); Image(systemName: existing.contains(recipe.id) ? "checkmark" : "plus") }
                            .frame(minHeight: 44)
                    }.disabled(adding || existing.contains(recipe.id))
                }
                if !searching, recipes.count < total { Button("Weitere Rezepte laden") { Task { await search(offset: recipes.count) } }.disabled(adding) }
                if !searching, recipes.isEmpty, error == nil { Text("Keine kochfertigen Rezepte gefunden.") }
            }
            .navigationTitle("Gericht wünschen")
            .searchable(text: $query, prompt: "Rezept suchen")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Schließen") { dismiss() }.disabled(adding) } }
            .interactiveDismissDisabled(adding)
            .task(id: "\(session.identity)-\(query)") {
                recipes = []; total = 0
                do { try await Task.sleep(for: .milliseconds(250)) } catch { return }
                await search()
            }
        }
    }

    private func search(offset: Int = 0) async {
        guard !adding, !session.readOnly, session.role != .guest else { return }
        let identity = session.identity, token = UUID(); requestID = token; searching = true; error = nil
        defer { if identity == session.identity, requestID == token { searching = false } }
        do {
            let result: WishRecipePage = try await session.api.kitchenRequest("/api/recipes", query: [
                .init(name: "search", value: query.trimmingCharacters(in: .whitespacesAndNewlines)),
                .init(name: "limit", value: "30"), .init(name: "offset", value: String(offset)),
                .init(name: "needs_manual_care", value: "false"),
            ])
            guard identity == session.identity, requestID == token, !Task.isCancelled else { return }
            recipes = offset == 0 ? result.items : recipes + result.items.filter { item in !recipes.contains(where: { $0.id == item.id }) }
            total = result.total
        } catch { if identity == session.identity, requestID == token, !Task.isCancelled { self.error = error.localizedDescription } }
    }

    private func add(_ recipe: HouseholdRecipe) async {
        guard !adding, !session.readOnly, session.role != .guest else { return }
        adding = true; requestID = UUID(); searching = false
        let identity = session.identity
        defer { if identity == session.identity { adding = false } }
        do {
            try await session.api.addMealWish(weekStart: weekStart, recipeID: recipe.id)
            guard identity == session.identity else { return }
            await onAdded()
            guard identity == session.identity else { return }
            dismiss()
        } catch { if identity == session.identity { self.error = error.localizedDescription; session.handle(error) } }
    }
}

private struct WishRecipePage: Decodable { let items: [HouseholdRecipe]; let total: Int }

@MainActor
struct RecipeWishSheet: View {
    let recipeID: Int
    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @StateObject private var state = HouseholdFeatureState()
    @State private var date = Date()
    var body: some View {
        NavigationStack {
            Form {
                Text("Wähle einen Tag der Woche, für die du dieses Gericht vorschlagen möchtest.")
                DatePicker("Woche auswählen", selection: $date, displayedComponents: .date)
                HouseholdFeedback(state: state)
                Button("Gericht für diese Woche wünschen") { Task { await save() } }.disabled(state.busy || session.readOnly)
            }
            .navigationTitle("Gericht wünschen")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Schließen") { dismiss() }.disabled(state.busy) } }
            .interactiveDismissDisabled(state.busy)
            .task(id: session.identity) { state.reset(session) }
        }
    }
    private func save() async {
        guard let token = state.begin(session) else { return }
        defer { state.finish(token, session) }
        let formatter = DateFormatter(); formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX"); formatter.dateFormat = "yyyy-MM-dd"
        do {
            try await session.api.addMealWish(weekStart: formatter.string(from: date), recipeID: recipeID)
            if state.current(token, session) { dismiss() }
        } catch { state.failed(error, token: token, session: session) }
    }
}
