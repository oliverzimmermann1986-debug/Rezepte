import SwiftUI

struct IngredientEditorView: View {
    let recipe: Recipe
    let onSaved: () async -> Void

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @State private var drafts: [EditableIngredient]
    @State private var isSaving = false

    init(recipe: Recipe, onSaved: @escaping () async -> Void) {
        self.recipe = recipe
        self.onSaved = onSaved
        _drafts = State(initialValue: recipe.ingredients.map {
            EditableIngredient(
                name: $0.name,
                amount: $0.amount.map { String($0) } ?? "",
                unit: $0.unit ?? ""
            )
        })
    }

    var body: some View {
        NavigationStack {
            List {
                ForEach($drafts) { $draft in
                    VStack(spacing: 10) {
                        TextField("Zutat", text: $draft.name)
                        HStack {
                            TextField("Menge", text: $draft.amount)
                                .keyboardType(.decimalPad)
                            TextField("Einheit", text: $draft.unit)
                        }
                    }
                    .textFieldStyle(.roundedBorder)
                }
                .onDelete { drafts.remove(atOffsets: $0) }

                Button {
                    drafts.append(EditableIngredient())
                } label: {
                    Label("Zutat hinzufügen", systemImage: "plus.circle.fill")
                }
            }
            .navigationTitle("Zutaten bearbeiten")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Abbrechen") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isSaving ? "Speichert …" : "Speichern") {
                        Task { await save() }
                    }
                    .disabled(isSaving || validDrafts.isEmpty)
                }
            }
        }
    }

    private var validDrafts: [IngredientDraft] {
        drafts.compactMap { draft in
            let name = draft.name.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !name.isEmpty else { return nil }
            let normalizedAmount = draft.amount.replacingOccurrences(of: ",", with: ".")
            return IngredientDraft(
                name: name,
                amount: Double(normalizedAmount),
                unit: draft.unit.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
            )
        }
    }

    private func save() async {
        isSaving = true
        defer { isSaving = false }
        do {
            _ = try await session.api.updateIngredients(id: recipe.id, ingredients: validDrafts)
            await onSaved()
            dismiss()
        } catch {
            session.handle(error)
        }
    }
}

private struct EditableIngredient: Identifiable {
    let id = UUID()
    var name = ""
    var amount = ""
    var unit = ""
}
