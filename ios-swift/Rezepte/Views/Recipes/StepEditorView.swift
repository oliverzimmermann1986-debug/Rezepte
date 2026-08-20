import SwiftUI

struct StepEditorView: View {
    let recipe: Recipe
    let onSaved: () async -> Void

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @State private var drafts: [EditableStep]
    @State private var isSaving = false

    init(recipe: Recipe, onSaved: @escaping () async -> Void) {
        self.recipe = recipe
        self.onSaved = onSaved
        _drafts = State(initialValue: recipe.steps.map {
            EditableStep(
                instruction: $0.instruction,
                minutes: $0.timerSeconds.map { String($0 / 60) } ?? ""
            )
        })
    }

    var body: some View {
        NavigationStack {
            List {
                ForEach(Array(drafts.indices), id: \.self) { index in
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Schritt \(index + 1)")
                            .font(.headline)
                        TextField(
                            "Zubereitung beschreiben",
                            text: $drafts[index].instruction,
                            axis: .vertical
                        )
                        .lineLimit(3...8)
                        TextField("Timer in Minuten (optional)", text: $drafts[index].minutes)
                            .keyboardType(.numberPad)
                    }
                    .padding(.vertical, 6)
                }
                .onDelete { drafts.remove(atOffsets: $0) }
                .onMove { drafts.move(fromOffsets: $0, toOffset: $1) }

                Button {
                    drafts.append(EditableStep())
                } label: {
                    Label("Schritt hinzufügen", systemImage: "plus.circle.fill")
                }
            }
            .environment(\.editMode, .constant(.active))
            .navigationTitle("Schritte bearbeiten")
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

    private var validDrafts: [StepDraft] {
        drafts.compactMap { draft in
            let text = draft.instruction.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { return nil }
            let seconds = Int(draft.minutes).map { max(0, $0 * 60) }
            return StepDraft(instruction: text, timerSeconds: seconds)
        }
    }

    private func save() async {
        isSaving = true
        defer { isSaving = false }
        do {
            _ = try await session.api.updateSteps(id: recipe.id, steps: validDrafts)
            await onSaved()
            dismiss()
        } catch {
            session.handle(error)
        }
    }
}

private struct EditableStep {
    var instruction = ""
    var minutes = ""
}

