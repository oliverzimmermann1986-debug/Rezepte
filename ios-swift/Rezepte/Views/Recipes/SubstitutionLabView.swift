import SwiftUI

struct SubstitutionLabView: View {
    let recipeID: Int
    let recipeName: String

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.recipeTheme) private var theme
    @State private var lab: SubstitutionLab?
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var selectedIngredientID: Int?
    @State private var selectedCandidateID: String?
    @State private var variantName = ""
    @State private var showApplyPrompt = false
    @State private var isApplying = false
    @State private var applyTask: Task<Void, Never>?
    @State private var createdVariant: String?
    @State private var createdReviewNotice: String?

    var body: some View {
        NavigationStack {
            Group {
                if isLoading && lab == nil {
                    ProgressView("Ersetzungen werden geladen …")
                } else if let errorMessage, lab == nil {
                    ErrorState(message: errorMessage) {
                        Task { await load() }
                    }
                } else if let lab {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 18) {
                            if let errorMessage {
                                feedbackCard(errorMessage)
                            }

                            if isApplying {
                                HStack(spacing: 12) {
                                    ProgressView()
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text("Variante wird sicher angelegt …")
                                            .font(.subheadline.bold())
                                        Text("Bitte geöffnet lassen, bis die Bestätigung erscheint.")
                                            .font(.caption)
                                            .foregroundStyle(theme.muted)
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .cardSurface()
                                .accessibilityElement(children: .combine)
                            }

                            safetyCard

                            if lab.items.isEmpty {
                                ContentUnavailableView(
                                    "Keine kuratierte Ersetzung",
                                    systemImage: "flask",
                                    description: Text("Für die Zutaten dieses Rezepts gibt es noch keinen geprüften Vorschlag.")
                                )
                            } else {
                                ForEach(lab.items) { ingredient in
                                    ingredientCard(ingredient)
                                }
                            }
                        }
                        .padding()
                    }
                    .background(theme.background)
                    .refreshable { await load() }
                }
            }
            .navigationTitle("Substitutionslabor")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Schließen") { dismiss() }
                        .disabled(isApplying)
                }
            }
            .task { await load() }
            .interactiveDismissDisabled(isApplying)
            .alert("Eigene Variante erstellen", isPresented: $showApplyPrompt) {
                TextField("Name der Variante", text: $variantName)
                Button("Variante erstellen") { beginApply() }
                    .disabled(
                        isApplying
                            || variantName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    )
                Button("Abbrechen", role: .cancel) {}
            } message: {
                Text("Das Original bleibt unverändert. Die neue Variante verliert Prüfstatus und berechnete Nährwerte, bis sie erneut geprüft wurde.")
            }
            .alert(
                "Variante erstellt",
                isPresented: Binding(
                    get: { createdVariant != nil },
                    set: { if !$0 { createdVariant = nil } }
                )
            ) {
                Button("Fertig") { dismiss() }
            } message: {
                Text(
                    createdReviewNotice
                        ?? "„\(createdVariant ?? "")“ wurde als eigenständiges Rezept angelegt."
                )
            }
        }
    }

    private var safetyCard: some View {
        VStack(alignment: .leading, spacing: 9) {
            Label("Vorschau mit manueller Freigabe", systemImage: "checkmark.shield")
                .font(.title3.bold())
            Text("Funktion, Mengenverhältnis, mögliche Allergene und Nährwertfolgen werden getrennt erklärt.")
                .font(.subheadline)
                .foregroundStyle(theme.muted)
            Label(
                "Keine medizinische Sicherheitsfreigabe: Rezept und Produktetiketten vollständig prüfen.",
                systemImage: "exclamationmark.triangle.fill"
            )
            .font(.caption.bold())
            .foregroundStyle(theme.warning)
        }
        .cardSurface()
    }

    private func ingredientCard(_ ingredient: SubstitutionIngredient) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Statt \(ingredientDisplay(ingredient))")
                .font(.title3.bold())

            ForEach(ingredient.candidates) { candidate in
                VStack(alignment: .leading, spacing: 9) {
                    HStack {
                        Text(candidate.replacementName)
                            .font(.headline)
                        Spacer()
                        Text(confidenceLabel(candidate.confidence))
                            .font(.caption.bold())
                            .foregroundStyle(theme.accentPressed)
                    }
                    substitutionPreview(ingredient: ingredient, candidate: candidate)
                    Label(candidate.functionalEffect, systemImage: "gearshape.2")
                        .font(.subheadline)
                    ForEach(candidate.allergenNotes, id: \.self) { note in
                        Label(note, systemImage: "allergens")
                            .font(.caption)
                            .foregroundStyle(theme.warning)
                    }
                    ForEach(candidate.nutritionNotes, id: \.self) { note in
                        Label(note, systemImage: "heart.text.square")
                            .font(.caption)
                            .foregroundStyle(theme.muted)
                    }
                    if let blockedTags = candidate.blockedAutoTags, !blockedTags.isEmpty {
                        Label(
                            "Keine automatische Freigabe als \(blockedTags.map(tagLabel).joined(separator: ", "))",
                            systemImage: "tag.slash"
                        )
                        .font(.caption.bold())
                        .foregroundStyle(theme.warning)
                    }
                    if candidate.requiresReview {
                        Label(
                            "Zubereitung und Produktetikett müssen vor dem Kochen manuell geprüft werden.",
                            systemImage: "person.crop.circle.badge.checkmark"
                        )
                        .font(.caption.bold())
                        .foregroundStyle(theme.warning)
                    }
                    Button {
                        selectedIngredientID = ingredient.ingredientId
                        selectedCandidateID = candidate.id
                        variantName = "\(recipeName) – mit \(candidate.replacementName)"
                        showApplyPrompt = true
                    } label: {
                        Label("Als Variante übernehmen", systemImage: "plus.square.on.square")
                            .frame(maxWidth: .infinity, minHeight: 42)
                    }
                    .buttonStyle(.bordered)
                    .disabled(isApplying)
                }
                .padding(14)
                .background(theme.background, in: RoundedRectangle(cornerRadius: 14))
            }
        }
        .cardSurface()
    }

    private func substitutionPreview(
        ingredient: SubstitutionIngredient,
        candidate: SubstitutionCandidate
    ) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            LabeledContent("Vorher", value: ingredientDisplay(ingredient))
            if let result = candidate.resultIngredient {
                LabeledContent("Nachher", value: result.displayText)
            } else {
                LabeledContent("Nachher", value: candidate.replacementName)
                Text("Legacy-Vorschau · Mengenfaktor \(candidate.ratio.formatted())")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(theme.muted)
            }
        }
        .font(.subheadline)
        .padding(10)
        .background(theme.surface, in: RoundedRectangle(cornerRadius: 10))
        .accessibilityElement(children: .combine)
    }

    private func feedbackCard(_ message: String) -> some View {
        Label(message, systemImage: "exclamationmark.triangle.fill")
            .font(.subheadline)
            .foregroundStyle(theme.danger)
            .frame(maxWidth: .infinity, alignment: .leading)
            .cardSurface()
    }

    private func ingredientDisplay(_ ingredient: SubstitutionIngredient) -> String {
        let amount = ingredient.amount.map {
            $0.rounded() == $0 ? String(Int($0)) : $0.formatted()
        }
        return [amount, ingredient.unit, ingredient.name]
            .compactMap { $0 }
            .joined(separator: " ")
    }

    private func confidenceLabel(_ value: String) -> String {
        switch value {
        case "high": "Hohe Passung"
        case "medium": "Mittlere Passung"
        default: "Prüfen"
        }
    }

    private func tagLabel(_ value: String) -> String {
        AllergenInfo(rawValue: value)?.title ?? value
    }

    private func load() async {
        guard !isApplying else { return }
        isLoading = lab == nil
        defer { isLoading = false }
        do {
            lab = try await session.api.recipeSubstitutions(id: recipeID)
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func beginApply() {
        guard applyTask == nil, !isApplying else { return }
        isApplying = true
        errorMessage = nil
        applyTask = Task { @MainActor in
            await apply()
            applyTask = nil
        }
    }

    private func apply() async {
        defer { isApplying = false }
        guard let ingredientID = selectedIngredientID,
              let candidateID = selectedCandidateID else { return }
        do {
            let result = try await session.api.applyRecipeSubstitution(
                id: recipeID,
                ingredientID: ingredientID,
                candidateID: candidateID,
                variantName: variantName.trimmingCharacters(in: .whitespacesAndNewlines)
            )
            createdVariant = result.name
            createdReviewNotice = result.substitution.reviewNotice
                ?? "„\(result.name)“ wurde angelegt. Zubereitung und Produktetiketten vor dem Kochen prüfen."
            errorMessage = nil
            NotificationCenter.default.post(name: .recipesChanged, object: recipeID)
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }
}
