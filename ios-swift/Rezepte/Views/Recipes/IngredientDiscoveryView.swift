import SwiftUI

private struct IngredientMatch: Decodable, Identifiable {
    let recipeId: Int
    let name: String
    let matchedIngredients: [String]
    let missingIngredients: [ShoppingPreview]
    let coverage: Double
    var id: Int { recipeId }
}
private struct IngredientMatches: Decodable { let items: [IngredientMatch]; let warnings: [String] }
private struct IngredientSearch: Encodable { let ingredients: [String]; let limit = 20 }
private struct MissingIngredientsRequest: Encodable {
    let requestId: String
    let recipeId: Int
    let ingredients: [String]
}

struct IngredientDiscoveryView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.recipeTheme) private var theme
    @State private var input = ""
    @State private var result: IngredientMatches?
    @State private var usedIngredients: [String] = []
    @State private var requestIDs: [Int: String] = [:]
    @State private var uncertain: Set<Int> = []
    @State private var saved: Set<Int> = []
    @State private var busy = false
    @State private var error: String?
    @State private var generation = UUID()
    @State private var active = true
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Text("Welche Zutaten hast du zu Hause?").font(.title2.bold())
                    Text("Der Abgleich prüft vorhandene Zutaten, keine Vorratsmengen.").foregroundStyle(.secondary)
                    TextField("Zum Beispiel Tomaten, Nudeln, Käse", text: $input, axis: .vertical)
                        .textFieldStyle(.roundedBorder).disabled(busy || !uncertain.isEmpty)
                    Button(busy ? "Wird abgeglichen …" : "Passende Rezepte finden") { Task { await search() } }
                        .buttonStyle(.borderedProminent).disabled(busy || !uncertain.isEmpty || input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || session.readOnly)
                    if let error { Text(error).foregroundStyle(theme.danger) }
                    if let result {
                        ForEach(result.warnings, id: \.self) { Text($0).font(.caption).foregroundStyle(.secondary) }
                        if result.items.isEmpty { Text("Keine passenden Rezepte gefunden.") }
                        ForEach(result.items) { item in
                            VStack(alignment: .leading, spacing: 12) {
                                NavigationLink(item.name) { RecipeDetailView(recipeID: item.recipeId) }.font(.headline)
                                Text("Vorhanden: \(item.matchedIngredients.joined(separator: ", "))").font(.subheadline)
                                Text(item.missingIngredients.isEmpty ? "Alle Zutaten vorhanden" : "Fehlt: \(item.missingIngredients.map(\.name).joined(separator: ", "))").font(.subheadline)
                                if !item.missingIngredients.isEmpty {
                                    Button(saved.contains(item.id) ? "Auf der Einkaufsliste" : uncertain.contains(item.id) ? "Hinzufügen erneut bestätigen" : "Fehlende Zutaten einkaufen") {
                                        Task { await addMissing(item) }
                                    }.buttonStyle(.bordered).disabled(busy || saved.contains(item.id) || session.readOnly)
                                }
                            }.cardSurface()
                        }
                    }
                }.padding()
            }.background(theme.background).navigationTitle("Zutaten nutzen")
                .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Schließen") { dismiss() } } }
                .onAppear { active = true }
                .onDisappear { active = false; generation = UUID() }
        }
    }
    @MainActor private func search() async {
        guard !busy, !session.readOnly, uncertain.isEmpty, active else { return }
        let ingredients = input.components(separatedBy: CharacterSet(charactersIn: ",;\n")).map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
        guard !ingredients.isEmpty, ingredients.count <= 100, ingredients.allSatisfy({ $0.count <= 200 }) else { error = "Bitte 1 bis 100 kurze Zutatennamen eingeben."; return }
        let identity = session.identity, request = UUID()
        generation = request; busy = true; error = nil
        defer { if identity == session.identity, generation == request { busy = false } }
        do {
            let value: IngredientMatches = try await session.api.kitchenRequest("/api/discovery/ingredients", method: "POST", body: IngredientSearch(ingredients: ingredients))
            guard session.identity == identity, generation == request, active else { return }
            result = value; usedIngredients = ingredients; requestIDs = [:]; saved = []
        } catch {
            guard session.identity == identity, generation == request, active else { return }
            self.error = error.localizedDescription; session.handle(error)
        }
    }
    @MainActor private func addMissing(_ item: IngredientMatch) async {
        guard !busy, !session.readOnly, !saved.contains(item.id), active else { return }
        let identity = session.identity, request = generation
        let id = requestIDs[item.id] ?? UUID().uuidString
        requestIDs[item.id] = id; busy = true; error = nil
        defer { if identity == session.identity, generation == request { busy = false } }
        do {
            let _: APIResult = try await session.api.kitchenRequest("/api/discovery/missing-to-cart", method: "POST", body: MissingIngredientsRequest(requestId: id, recipeId: item.id, ingredients: usedIngredients))
            guard session.identity == identity, generation == request, active else { return }
            saved.insert(item.id); uncertain.remove(item.id)
        } catch {
            guard session.identity == identity, generation == request, active else { return }
            uncertain.insert(item.id)
            self.error = "Die Bestätigung fehlt. Wiederholen verwendet dieselbe Anfrage, damit nichts doppelt eingetragen wird. \(error.localizedDescription)"
            session.handle(error)
        }
    }
}
