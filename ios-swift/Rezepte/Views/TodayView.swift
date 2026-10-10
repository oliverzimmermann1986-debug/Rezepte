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

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    Text(Date().formatted(.dateTime.weekday(.wide).day().month(.wide)))
                        .font(.subheadline).foregroundStyle(theme.muted)
                    if session.readOnly {
                        Text("Was möchtest du heute kochen?").font(.title2.bold())
                        NavigationLink("Rezepte entdecken") { RecipesView() }
                        Text("Mit einem Konto planst du die Woche und teilst Einkäufe mit deinem Haushalt.")
                    } else if loadedIdentity == session.identity {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("Heute auf dem Tisch").font(.title2.bold())
                            if loadingMeals { ProgressView("Plan wird aktualisiert …") }
                            if let mealError { retryMessage(mealError) }
                            if let week {
                                let entries = week.days.first { $0.date == day }?.items ?? []
                                if entries.isEmpty { Text("Für heute ist noch kein Gericht geplant.") }
                                ForEach(entries) { item in
                                    NavigationLink { RecipeDetailView(recipeID: item.recipeId) } label: {
                                        VStack(alignment: .leading) {
                                            Text(item.recipeName).font(.headline)
                                            Text("\(item.plannedServings) Portionen · Rezept öffnen & kochen").font(.caption)
                                        }.frame(minHeight: 48, alignment: .leading)
                                    }
                                }
                            }
                            NavigationLink("Wochenplan öffnen") { MealPlanView() }.frame(minHeight: 44)
                        }.cardSurface()
                        VStack(alignment: .leading, spacing: 12) {
                            Text("Noch einkaufen").font(.title2.bold())
                            if shopping.isReady {
                                let open = shopping.items.filter { !$0.checked }
                                Text("\(open.count) offene Artikel").font(.title.bold())
                                ForEach(Array(open.prefix(4))) { item in Text(item.name) }
                                if shopping.pendingCount > 0 { Text("\(shopping.pendingCount) Änderungen warten auf Abgleich.").font(.caption) }
                                if shopping.conflictCount > 0 { Text("Bitte prüfe die Konflikte in der Einkaufsliste.").foregroundStyle(theme.warning) }
                            } else { Text("Die Einkaufsliste ist noch nicht verfügbar.") }
                            if let error = shopping.errorMessage { Text(error).font(.caption).foregroundStyle(theme.warning) }
                            NavigationLink("Einkaufsliste öffnen") { CartView() }.frame(minHeight: 44)
                        }.cardSurface()
                        VStack(alignment: .leading, spacing: 12) {
                            Text("Wünsche für diese Woche").font(.title2.bold())
                            if loadingWishes { ProgressView("Wünsche werden aktualisiert …") }
                            if let wishError { retryMessage(wishError) }
                            if let wishes {
                                let open = wishes.filter { $0.plannedFor == nil }.sorted { $0.votes > $1.votes }
                                if open.isEmpty { Text("Keine offenen Wünsche.") }
                                ForEach(Array(open.prefix(3))) { wish in
                                    NavigationLink { RecipeDetailView(recipeID: wish.recipeId) } label: {
                                        HStack { Text(wish.recipeName); Spacer(); Text("\(wish.votes) Stimmen").font(.caption) }
                                            .frame(minHeight: 48)
                                    }
                                }
                            }
                            NavigationLink("Im Wochenplan abstimmen") { MealPlanView() }.frame(minHeight: 44)
                        }.cardSurface()
                        NavigationLink("Mit vorhandenen Zutaten kochen") { IngredientDiscoveryView() }
                            .frame(minHeight: 48)
                    }
                }.padding()
            }
            .background(theme.background).navigationTitle("Heute")
            .refreshable { await refresh() }
            .task(id: session.identity) {
                active = true
                if loadedIdentity != session.identity {
                    week = nil; wishes = nil; mealError = nil; wishError = nil
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
