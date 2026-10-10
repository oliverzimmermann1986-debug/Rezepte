import SwiftUI

enum KitchenDay {
    static func string(_ date: Date = Date(), calendar: Calendar = .current) -> String {
        // API dates are Gregorian even when the user's display calendar is not.
        var gregorian = Calendar(identifier: .gregorian)
        gregorian.timeZone = calendar.timeZone
        let parts = gregorian.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 1970, parts.month ?? 1, parts.day ?? 1)
    }
}

private struct TodayWish: Decodable, Identifiable {
    let id: Int
    let recipeId: Int
    let recipeName: String
    let votes: Int
    let plannedFor: String?
}
private struct TodayWishes: Decodable { let items: [TodayWish] }

struct TodayWishesRoute: Identifiable {
    let id = UUID()
    let identity: UUID
    let day: String
    let cachedWeek: MealWeek?

    init(identity: UUID, day: String, cachedWeek: MealWeek?) {
        self.identity = identity
        self.day = day
        // The date can change while Today still shows the previous week's snapshot.
        self.cachedWeek = cachedWeek?.days.contains(where: { $0.date == day }) == true ? cachedWeek : nil
    }
}

@MainActor
struct TodayView: View {
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var shopping: ShoppingSyncStore
    @Environment(\.recipeTheme) private var theme
    @Environment(\.scenePhase) private var scenePhase
    @State private var day = KitchenDay.string()
    @State private var week: MealWeek?
    @State private var wishes: [TodayWish]?
    @State private var mealError: String?
    @State private var wishError: String?
    @State private var loadingMeals = false
    @State private var loadingWishes = false
    @State private var generation = UUID()
    @State private var active = false
    @State private var loadedIdentity: UUID?
    @State private var selectedDay: MealDay?
    @State private var wishesRoute: TodayWishesRoute?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    Text(Date().formatted(.dateTime.weekday(.wide).day().month(.wide)))
                        .font(.subheadline).foregroundStyle(theme.muted)
                    if session.readOnly {
                        Text("Was möchtest du heute kochen?").font(.title2.bold())
                        NavigationLink("Rezepte entdecken") { RecipesView() }
                            .frame(minHeight: 44)
                        Text("Mit einem Konto planst du die Woche und teilst Einkäufe mit deinem Haushalt.")
                    } else if loadedIdentity == session.identity {
                        mealsCard
                        shoppingCard
                        wishesCard
                        NavigationLink("Mit vorhandenen Zutaten kochen") { IngredientDiscoveryView() }
                            .frame(minHeight: 48)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding()
            }
            .background(theme.background).navigationTitle("Heute")
            .foregroundStyle(theme.ink)
            .refreshable { await refresh() }
            .sheet(item: $selectedDay, onDismiss: { Task { await refresh() } }) { selected in
                RecipePickerView(day: selected) { await refresh() }
                    .id(session.identity)
            }
            .sheet(item: $wishesRoute, onDismiss: { Task { await refresh() } }) { route in
                TodayWishesSheet(route: route) { await refresh() }
                    .id(session.identity)
            }
            .task(id: session.identity) {
                active = true
                if loadedIdentity != session.identity {
                    week = nil; wishes = nil; mealError = nil; wishError = nil
                    selectedDay = nil; wishesRoute = nil
                    loadedIdentity = session.identity
                }
                await refresh()
                while !Task.isCancelled {
                    try? await Task.sleep(for: .seconds(30))
                    guard !Task.isCancelled else { break }
                    if scenePhase == .active, active, day != KitchenDay.string() { await refresh() }
                }
            }
            .onAppear { active = true }
            .onDisappear { active = false; generation = UUID() }
            .onChange(of: scenePhase) { _, phase in if phase == .active && active { Task { await refresh() } } }
        }
    }

    private var mealsCard: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Heute auf dem Tisch")
                .font(.headline).foregroundStyle(theme.muted)
                .accessibilityAddTraits(.isHeader)
            if loadingMeals { ProgressView("Plan wird aktualisiert …") }
            if let mealError { retryMessage(mealError) }
            if let today = week?.days.first(where: { $0.date == day }) {
                if today.items.isEmpty {
                    Text("Was möchtest du heute kochen?").font(.title2.bold())
                    Text("Wähle ein Gericht und die passenden Portionen für heute.")
                        .foregroundStyle(theme.muted)
                    Button { selectedDay = today } label: {
                        Label("Gericht für heute planen", systemImage: "plus")
                            .frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(theme.accentSoft).foregroundStyle(theme.ink)
                    .accessibilityIdentifier("today.add-meal")
                }
                ForEach(today.items) { item in
                    if item.id != today.items.first?.id { Divider() }
                    VStack(alignment: .leading, spacing: 6) {
                        Text(item.recipeName).font(.title2.bold())
                        Text("\(item.plannedServings) \(item.plannedServings == 1 ? "Portion" : "Portionen")")
                            .font(.subheadline).foregroundStyle(theme.muted)
                    }
                    .accessibilityElement(children: .combine)
                    NavigationLink { RecipeDetailView(recipeID: item.recipeId) } label: {
                        Label("Rezept öffnen", systemImage: "book")
                            .frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(theme.accentSoft).foregroundStyle(theme.ink)
                    .accessibilityLabel("\(item.recipeName), Rezept öffnen")
                    .accessibilityHint("Öffnet Zutaten und Zubereitung.")
                    .accessibilityIdentifier("today.recipe.\(item.recipeId)")
                }
            }
            NavigationLink("Wochenplan öffnen") { MealPlanView() }.frame(minHeight: 44)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .cardSurface()
    }

    private var shoppingCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Noch einkaufen").font(.headline).accessibilityAddTraits(.isHeader)
            if shopping.isReady {
                let open = shopping.items.filter { !$0.checked }
                if open.isEmpty {
                    Text(shopping.items.isEmpty ? "Deine Einkaufsliste ist leer." : "Alles auf deiner Liste ist eingekauft.")
                    Text("Öffne die Liste, um weitere Artikel hinzuzufügen.")
                        .font(.subheadline).foregroundStyle(theme.muted)
                } else {
                    Text("\(open.count) \(open.count == 1 ? "offener Artikel" : "offene Artikel")")
                        .font(.subheadline.weight(.semibold))
                    Text(open.prefix(3).map(\.name).joined(separator: ", ") + (open.count > 3 ? " …" : ""))
                        .font(.subheadline).foregroundStyle(theme.muted)
                }
                if shopping.waitingCount > 0 {
                    Text("\(shopping.waitingCount) \(shopping.waitingCount == 1 ? "Änderung wartet" : "Änderungen warten") auf Übertragung.")
                        .font(.caption).foregroundStyle(theme.muted)
                }
                if shopping.conflictCount > 0 {
                    Text("\(shopping.conflictCount) \(shopping.conflictCount == 1 ? "Änderung braucht" : "Änderungen brauchen") deine Entscheidung in der Einkaufsliste.")
                        .font(.subheadline).foregroundStyle(theme.warning)
                }
            } else {
                Text("Die Einkaufsliste ist noch nicht verfügbar. Öffne sie, um den Abgleich zu prüfen.")
                    .foregroundStyle(theme.muted)
            }
            if let error = shopping.errorMessage { Text(error).font(.caption).foregroundStyle(theme.warning) }
            NavigationLink("Einkaufsliste öffnen") { CartView() }.frame(minHeight: 44)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .cardSurface()
    }

    private var wishesCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Wünsche für diese Woche").font(.headline).accessibilityAddTraits(.isHeader)
            if loadingWishes { ProgressView("Wünsche werden aktualisiert …") }
            if let wishError { retryMessage(wishError) }
            if let wishes {
                let open = wishes.filter { $0.plannedFor == nil }.sorted {
                    $0.votes == $1.votes ? $0.id < $1.id : $0.votes > $1.votes
                }
                if open.isEmpty {
                    Text(wishes.isEmpty ? "Was soll diese Woche auf den Tisch?" : "Alle Wünsche sind bereits eingeplant.")
                    Text("Öffne die Wochenwünsche und schlage deinem Haushalt ein Gericht vor.")
                        .font(.subheadline).foregroundStyle(theme.muted)
                }
                ForEach(Array(open.prefix(3))) { wish in
                    NavigationLink { RecipeDetailView(recipeID: wish.recipeId) } label: {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(wish.recipeName).font(.subheadline.weight(.semibold))
                            Text("\(wish.votes) \(wish.votes == 1 ? "Stimme" : "Stimmen")")
                                .font(.caption).foregroundStyle(theme.muted)
                        }
                        .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityHint("Öffnet das Rezept. Zum Abstimmen nutze den Knopf unter den Wünschen.")
                }
            }
            Button {
                wishesRoute = TodayWishesRoute(identity: session.identity, day: KitchenDay.string(), cachedWeek: week)
            } label: {
                Label(wishes?.contains(where: { $0.plannedFor == nil }) == true ? "Jetzt abstimmen" : "Wochenwünsche öffnen", systemImage: "hand.thumbsup")
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            }
            .buttonStyle(.bordered)
            .accessibilityIdentifier("today.wishes")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .cardSurface()
    }

    private func retryMessage(_ value: String) -> some View {
        VStack(alignment: .leading) {
            Text(value).font(.caption).foregroundStyle(theme.warning)
            Button("Erneut versuchen") { Task { await refresh() } }.frame(minHeight: 44)
        }
    }
    @MainActor private func refresh() async {
        guard !session.readOnly, session.role != .guest, active, !Task.isCancelled else { return }
        let identity = session.identity, request = UUID(), today = KitchenDay.string()
        generation = request
        if day != today { week = nil; wishes = nil; day = today }
        loadingMeals = true; loadingWishes = true; mealError = nil; wishError = nil
        async let meals: Void = loadMeals(day: today, identity: identity, request: request)
        async let wanted: Void = loadWishes(day: today, identity: identity, request: request)
        async let cart: Void = refreshShopping(identity: identity)
        _ = await (meals, wanted, cart)
    }
    @MainActor private func refreshShopping(identity: UUID) async {
        guard session.identity == identity, active, !Task.isCancelled else { return }
        await shopping.activate(session: session)
        guard session.identity == identity, active, !Task.isCancelled else { return }
        try? await shopping.refresh()
    }
    @MainActor private func loadMeals(day: String, identity: UUID, request: UUID) async {
        do {
            let value = try await session.api.mealWeek(start: day)
            guard session.identity == identity, generation == request, active, !Task.isCancelled else { return }
            week = value
        } catch {
            guard session.identity == identity, generation == request, active, !Task.isCancelled else { return }
            if case APIError.unauthenticated = error { session.handle(error); return }
            mealError = week == nil ? "Der Wochenplan ist gerade nicht erreichbar." : "Zuletzt geladener Plan; Aktualisierung fehlgeschlagen."
        }
        loadingMeals = false
    }
    @MainActor private func loadWishes(day: String, identity: UUID, request: UUID) async {
        do {
            let value: TodayWishes = try await session.api.kitchenRequest("/api/meal-wishes", query: [.init(name: "week_start", value: day)])
            guard session.identity == identity, generation == request, active, !Task.isCancelled else { return }
            wishes = value.items
        } catch {
            guard session.identity == identity, generation == request, active, !Task.isCancelled else { return }
            if case APIError.unauthenticated = error { session.handle(error); return }
            wishError = wishes == nil ? "Die Haushaltswünsche sind gerade nicht erreichbar." : "Zuletzt geladene Wünsche; Aktualisierung fehlgeschlagen."
        }
        loadingWishes = false
    }
}

@MainActor
private struct TodayWishesSheet: View {
    let route: TodayWishesRoute
    let onApplied: () async -> Void
    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @State private var week: MealWeek?
    @State private var errorMessage: String?
    @State private var loading = false
    @State private var requestID = UUID()

    init(route: TodayWishesRoute, onApplied: @escaping () async -> Void) {
        self.route = route
        self.onApplied = onApplied
        _week = State(initialValue: route.cachedWeek)
    }

    var body: some View {
        Group {
            if route.identity != session.identity {
                EmptyView()
            } else if let week {
                MealWishesView(week: week, onApplied: onApplied)
            } else {
                NavigationStack {
                    Group {
                        if let errorMessage {
                            ErrorState(message: errorMessage) { Task { await loadWeek() } }
                        } else {
                            ProgressView("Wochenwünsche werden geöffnet …")
                        }
                    }
                    .navigationTitle("Wochenwünsche")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Schließen") { dismiss() } } }
                }
            }
        }
        .task { if week == nil { await loadWeek() } }
        .onDisappear { requestID = UUID() }
    }

    private func loadWeek() async {
        guard route.identity == session.identity, !loading, !session.readOnly, session.role != .guest else { return }
        let identity = session.identity, request = UUID()
        requestID = request; loading = true; errorMessage = nil
        defer { if requestID == request { loading = false } }
        do {
            let result = try await session.api.mealWeek(start: route.day)
            guard session.identity == identity, requestID == request, !Task.isCancelled else { return }
            week = result
        } catch {
            guard session.identity == identity, requestID == request, !Task.isCancelled else { return }
            session.handle(error)
            errorMessage = "Die Woche ist gerade nicht erreichbar. Bitte versuche es erneut."
        }
    }
}
