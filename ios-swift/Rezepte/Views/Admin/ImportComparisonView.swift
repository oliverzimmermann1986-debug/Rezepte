import SwiftUI

/// Explicit source choices: quantities are never silently added together.
struct ImportComparisonView: View {
    let request: ImportComparisonRequest
    let onResolved: () async -> Void

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @State private var result: ImportComparisonResponse?
    @State private var selected: ImportComparisonCandidate?
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text("Verglichen werden Name und Zutaten deines Imports mit erreichbaren Rezepten. Ähnlichkeit ist ein Hinweis, keine sichere Dublette.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                if let result {
                    if result.items.isEmpty {
                        ContentUnavailableView("Kein ähnliches Rezept", systemImage: "doc.text.magnifyingglass",
                            description: Text("Du kannst den Import wie gewohnt speichern."))
                    }
                    ForEach(result.items) { candidate in
                        Button { selected = candidate } label: {
                            VStack(alignment: .leading, spacing: 6) {
                                Text(candidate.name).font(.headline).foregroundStyle(.primary)
                                Text(candidate.url.flatMap { URL(string: $0)?.host } ?? "Eigene Rezeptquelle")
                                    .font(.caption).foregroundStyle(.secondary)
                                Text("\(candidate.sharedIngredients.count) gemeinsame Zutaten · \(candidate.score) % Ähnlichkeit")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            .padding(.vertical, 4)
                        }
                    }
                    if result.truncated {
                        Text("Die Suche umfasst die 2.000 zuletzt gespeicherten Rezepte. Ältere Rezepte sind hier möglicherweise nicht enthalten.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                } else if errorMessage == nil {
                    ProgressView("Rezepte vergleichen …")
                }
                if let errorMessage {
                    Section {
                        Text(errorMessage).foregroundStyle(.red)
                        Button("Erneut versuchen") { Task { await load() } }
                    }
                }
            }
            .navigationTitle("Ähnliche Importe")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Schließen") { dismiss() } } }
            .task { await load() }
            .sheet(item: $selected) { candidate in
                ImportComparisonChoiceView(request: request, candidate: candidate) {
                    await onResolved()
                    dismiss()
                }
            }
        }
    }

    private func load() async {
        let identity = session.identity
        errorMessage = nil
        do {
            let response = try await session.api.compareImport(request)
            guard identity == session.identity else { return }
            result = response
        } catch {
            guard identity == session.identity else { return }
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }
}

private struct ImportComparisonChoiceView: View {
    let request: ImportComparisonRequest
    let candidate: ImportComparisonCandidate
    let onResolved: () async -> Void

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @State private var action = "merge"
    @State private var name = ""
    @State private var ingredientsFrom = "import"
    @State private var stepsFrom = "existing"
    @State private var requestID = UUID()
    @State private var isWorking = false
    @State private var confirm = false
    @State private var errorMessage: String?

    private var canCreate: Bool {
        if action == "keep" { return true }
        let ingredients = action == "variant" || ingredientsFrom == "import" ? request.draft.ingredients : candidate.ingredients
        let steps = action == "variant" || stepsFrom == "import" ? request.draft.steps : candidate.steps
        return !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !ingredients.isEmpty && !steps.isEmpty
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Dein Import") {
                    Text(request.draft.name).font(.headline)
                    Text(request.url).font(.caption).textSelection(.enabled)
                    recipeContent(ingredients: request.draft.ingredients, steps: request.draft.steps, servings: request.draft.servings)
                }
                Section("Vorhandenes Rezept") {
                    Text(candidate.name).font(.headline)
                    if let url = candidate.url { Text(url).font(.caption).textSelection(.enabled) }
                    recipeContent(ingredients: candidate.ingredients, steps: candidate.steps, servings: candidate.servings)
                }
                Section("Unterschiede") {
                    difference("Nur im Import", names: candidate.importOnlyIngredients)
                    difference("Nur im vorhandenen Rezept", names: candidate.existingOnlyIngredients)
                    Text("Mengen und Portionen oben direkt vergleichen. Die Zutatenübereinstimmung berücksichtigt keine Mengen.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("Deine Entscheidung") {
                    Picker("Aktion", selection: $action) {
                        Text("Vorhandenes behalten").tag("keep")
                        Text("Import als eigene Variante").tag("variant")
                        Text("Als neue Variante zusammenführen").tag("merge")
                    }
                    .pickerStyle(.menu)
                    if action != "keep" {
                        TextField("Name der eigenen Variante", text: $name)
                        if action == "merge" {
                            Picker("Zutaten und Portionen", selection: $ingredientsFrom) {
                                Text("Aus dem Import").tag("import")
                                Text("Aus dem vorhandenen Rezept").tag("existing")
                            }
                            Picker("Zubereitung", selection: $stepsFrom) {
                                Text("Aus dem Import").tag("import")
                                Text("Aus dem vorhandenen Rezept").tag("existing")
                            }
                        }
                        Text("Es entsteht ein neues Rezept mit dem Bild des vorhandenen Rezepts. Zutaten und Schritte werden als vollständige Listen übernommen. Bitte prüfe, ob sie zusammenpassen.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Text(action == "keep"
                         ? "Der Import wird erledigt und das vorhandene Rezept in deinem Haushalt gespeichert. Änderungen am Import werden verworfen."
                         : "Das vorhandene Rezept mit seinen Notizen, Planungen und Einkaufsbezügen bleibt erhalten. Der Import wird danach erledigt.")
                        .font(.caption).foregroundStyle(.secondary)
                    Button(isWorking ? "Wird übernommen …" : "Entscheidung übernehmen") { confirm = true }
                        .disabled(isWorking || !canCreate || session.readOnly || !session.canImport)
                }
                if let errorMessage { Section { Text(errorMessage).foregroundStyle(.red) } }
            }
            .disabled(isWorking)
            .navigationTitle("Quellen vergleichen")
            .navigationBarTitleDisplayMode(.inline)
            .interactiveDismissDisabled(isWorking)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Zurück") { dismiss() }.disabled(isWorking) } }
            .onAppear { if name.isEmpty { name = String((request.draft.name + " – meine Variante").prefix(200)) } }
            .confirmationDialog("Import abschließen?", isPresented: $confirm, titleVisibility: .visible) {
                Button("Entscheidung übernehmen") { Task { await resolve() } }
                Button("Abbrechen", role: .cancel) {}
            } message: {
                Text(action == "keep" ? "Deine Änderungen am Import werden verworfen. Das vorhandene Rezept bleibt erhalten."
                     : "Eine eigene Variante wird angelegt. Bitte Zutaten, Portionen und Zubereitung danach zusammen prüfen.")
            }
        }
    }

    @ViewBuilder private func recipeContent(ingredients: [PendingIngredient], steps: [PendingStep], servings: Int?) -> some View {
        Text(servings.map { "\($0) Portionen" } ?? "Portionen nicht angegeben").font(.caption).foregroundStyle(.secondary)
        DisclosureGroup("\(ingredients.count) Zutaten") {
            ForEach(Array(ingredients.enumerated()), id: \.offset) { _, ingredient in
                Text([ingredient.amount.map { $0.formatted() }, ingredient.unit, ingredient.name]
                    .compactMap { $0 }.joined(separator: " "))
            }
        }
        DisclosureGroup("\(steps.count) Schritte") {
            ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                Text("\(index + 1). \(step.instruction)")
            }
        }
    }

    private func difference(_ title: String, names: [String]) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.subheadline.bold())
            Text(names.isEmpty ? "Keine" : names.joined(separator: ", ")).font(.subheadline)
        }
    }

    private func resolve() async {
        guard session.canImport, !session.readOnly, !isWorking, canCreate else { return }
        let identity = session.identity
        isWorking = true
        errorMessage = nil
        defer { isWorking = false }
        do {
            let response = try await session.api.resolveImportComparison(ImportComparisonResolution(
                url: request.url, visibility: request.visibility, draft: request.draft,
                requestId: requestID, recipeId: candidate.id, snapshot: candidate.snapshot,
                action: action, name: name.trimmingCharacters(in: .whitespacesAndNewlines),
                ingredientsFrom: ingredientsFrom, stepsFrom: stepsFrom))
            guard identity == session.identity else { return }
            guard response.ok != false else { throw APIError.server(409, response.message ?? "Import konnte nicht abgeschlossen werden.") }
            await onResolved()
        } catch {
            guard identity == session.identity else { return }
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }
}
