import PhotosUI
import SwiftUI
import UIKit

struct PendingEditorView: View {
    let item: PendingItem
    let onChanged: () async -> Void

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL
    @Environment(\.recipeTheme) private var theme
    @State private var name: String
    @State private var recipeType: String
    @State private var category: String
    @State private var description: String
    @State private var servings: String
    @State private var ingredients: [PendingIngredientDraft]
    @State private var steps: [PendingStepDraft]
    @State private var verified = false
    @State private var isSaving = false
    @State private var isPhotoScanning = false
    @State private var isReanalyzing = false
    @State private var selectedPhoto: PhotosPickerItem?
    @State private var statusMessage: String?
    @State private var errorMessage: String?
    @State private var showDiscardConfirmation = false

    init(item: PendingItem, onChanged: @escaping () async -> Void) {
        self.item = item
        self.onChanged = onChanged
        let suggestion = item.aiSuggestion
        _name = State(initialValue: suggestion?.name ?? suggestion?.filename?.deletingPathExtension ?? "")
        _recipeType = State(initialValue: suggestion?.type ?? "Hauptgericht")
        _category = State(initialValue: suggestion?.category ?? "Allgemein")
        _description = State(initialValue: item.description ?? "")
        _servings = State(initialValue: suggestion?.servings.map(String.init) ?? "")
        _ingredients = State(initialValue: Self.ingredientDrafts(from: suggestion))
        _steps = State(initialValue: Self.stepDrafts(from: suggestion))
    }

    private var isBusy: Bool { isSaving || isPhotoScanning || isReanalyzing }
    private var hasIngredients: Bool {
        ingredients.contains { !$0.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Zuordnung") {
                    TextField("Rezeptname", text: $name)
                    TextField("Typ", text: $recipeType)
                    TextField("Kategorie", text: $category)
                    TextField("Portionen", text: $servings)
                        .keyboardType(.numberPad)
                    TextField("Erkannter Text", text: $description, axis: .vertical)
                        .lineLimit(4...10)
                }

                Section {
                    PhotosPicker(selection: $selectedPhoto, matching: .images) {
                        Label(
                            isPhotoScanning ? "Foto wird gescannt …" : "Foto hinzufügen und scannen",
                            systemImage: "photo.badge.plus"
                        )
                    }
                    .disabled(isBusy)

                    Button {
                        Task { await reanalyze() }
                    } label: {
                        Label(
                            isReanalyzing ? "KI prüft erneut …" : "Nochmals mit KI prüfen",
                            systemImage: "sparkles"
                        )
                    }
                    .disabled(isBusy)

                    Text("Der Foto-Scan ergänzt das Rezeptbild und liest Zutaten sowie Schritte neu ein.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } header: {
                    Text("Analyse")
                }

                Section("Zutaten") {
                    ForEach($ingredients) { $ingredient in
                        VStack(alignment: .leading, spacing: 10) {
                            TextField("Zutat", text: $ingredient.name)
                            HStack {
                                TextField("Menge", text: $ingredient.amount)
                                    .keyboardType(.decimalPad)
                                TextField("Einheit", text: $ingredient.unit)
                            }
                            Button(role: .destructive) {
                                ingredients.removeAll { $0.id == ingredient.id }
                            } label: {
                                Label("Zutat entfernen", systemImage: "minus.circle")
                            }
                            .font(.callout)
                        }
                        .padding(.vertical, 5)
                    }

                    Button {
                        ingredients.append(PendingIngredientDraft())
                    } label: {
                        Label("Zutat hinzufügen", systemImage: "plus.circle.fill")
                    }
                }

                Section("Zubereitung") {
                    ForEach(Array(steps.indices), id: \.self) { index in
                        VStack(alignment: .leading, spacing: 10) {
                            Text("Schritt \(index + 1)")
                                .font(.headline)
                            TextField(
                                "Zubereitung beschreiben",
                                text: $steps[index].instruction,
                                axis: .vertical
                            )
                            .lineLimit(3...8)
                            TextField("Timer in Sekunden (optional)", text: $steps[index].timerSeconds)
                                .keyboardType(.numberPad)
                            Button(role: .destructive) {
                                steps.remove(at: index)
                            } label: {
                                Label("Schritt entfernen", systemImage: "minus.circle")
                            }
                            .font(.callout)
                        }
                        .padding(.vertical, 5)
                    }

                    Button {
                        steps.append(PendingStepDraft())
                    } label: {
                        Label("Schritt hinzufügen", systemImage: "plus.circle.fill")
                    }
                }

                Section {
                    Toggle(isOn: $verified) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text("Zutaten geprüft")
                            Text("Bestätigt nur die von dir kontrollierte Zutatenliste.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .disabled(!hasIngredients)
                }

                Section("Quelle") {
                    Text(item.url)
                        .font(.caption)
                        .textSelection(.enabled)
                    if let sourceURL = safeExternalURL(item.url) {
                        Button {
                            openURL(sourceURL)
                        } label: {
                            Label("Originalquelle öffnen", systemImage: "arrow.up.right.square")
                        }
                    }
                    if let filename = item.aiSuggestion?.filename?.nilIfEmpty {
                        LabeledContent("Datei", value: filename)
                    }
                    if let confidence = item.aiSuggestion?.confidence {
                        LabeledContent("KI-Sicherheit", value: confidence.formatted(.percent.precision(.fractionLength(0))))
                    }
                }

                if let statusMessage {
                    Section {
                        Label(statusMessage, systemImage: "checkmark.circle")
                            .foregroundStyle(theme.success)
                    }
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage)
                            .foregroundStyle(theme.danger)
                            .accessibilityLabel("Fehler: \(errorMessage)")
                    }
                }

                Section {
                    Button(role: .destructive) {
                        showDiscardConfirmation = true
                    } label: {
                        Label("Import verwerfen", systemImage: "trash")
                    }
                    .disabled(isBusy)
                }
            }
            .navigationTitle("Import prüfen")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Abbrechen") { dismiss() }
                        .disabled(isBusy)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isSaving ? "Speichert …" : "Speichern") {
                        Task { await save() }
                    }
                    .disabled(isBusy || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .onChange(of: selectedPhoto) { _, photo in
                guard let photo else { return }
                Task { await scanPhoto(photo) }
            }
            .confirmationDialog(
                "Import wirklich verwerfen?",
                isPresented: $showDiscardConfirmation,
                titleVisibility: .visible
            ) {
                Button("Verwerfen", role: .destructive) {
                    Task { await discard() }
                }
                Button("Abbrechen", role: .cancel) {}
            } message: {
                Text("Der Eingang wird als verworfen markiert und verschwindet aus der Prüfung.")
            }
        }
    }

    private func scanPhoto(_ photo: PhotosPickerItem) async {
        isPhotoScanning = true
        errorMessage = nil
        statusMessage = nil
        defer {
            isPhotoScanning = false
            selectedPhoto = nil
        }
        do {
            guard let original = try await photo.loadTransferable(type: Data.self),
                  let image = UIImage(data: original),
                  let data = image.jpegData(compressionQuality: 0.9) else {
                throw PendingValidationError.invalidPhoto
            }
            let result = try await session.api.scanPendingPhoto(
                url: item.url,
                data: data,
                filename: "rezeptfoto-\(Int(Date().timeIntervalSince1970)).jpg",
                mimeType: "image/jpeg"
            )
            await apply(result, fallbackMessage: "Das Foto wurde erkannt.")
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func reanalyze() async {
        isReanalyzing = true
        errorMessage = nil
        statusMessage = nil
        defer { isReanalyzing = false }
        do {
            let result = try await session.api.reanalyzePending(url: item.url)
            await apply(result, fallbackMessage: "Der KI-Vorschlag wurde aktualisiert.")
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func apply(_ result: PendingAnalysisResult, fallbackMessage: String) async {
        guard result.ok else {
            errorMessage = result.error ?? result.message ?? "Die Analyse ist fehlgeschlagen."
            return
        }
        if result.action == "auto_saved" || result.action == "already_saved" {
            await onChanged()
            dismiss()
            return
        }
        if let suggestion = result.analysis {
            name = suggestion.name?.nilIfEmpty ?? name
            recipeType = suggestion.type?.nilIfEmpty ?? recipeType
            category = suggestion.category?.nilIfEmpty ?? category
            if let value = suggestion.servings { servings = String(value) }
            if let values = suggestion.ingredients, !values.isEmpty {
                ingredients = values.map(PendingIngredientDraft.init)
            }
            if let values = suggestion.steps, !values.isEmpty {
                steps = values.map(PendingStepDraft.init)
            }
        }
        description = result.description?.nilIfEmpty ?? description
        verified = false
        statusMessage = result.message ?? fallbackMessage
        await onChanged()
    }

    private func save() async {
        isSaving = true
        errorMessage = nil
        statusMessage = nil
        defer { isSaving = false }
        do {
            let cleanServings = try parsedServings()
            let cleanIngredients = try parsedIngredients()
            let cleanSteps = try parsedSteps()
            let result = try await session.api.resolvePending(
                url: item.url,
                action: "save",
                name: name.trimmingCharacters(in: .whitespacesAndNewlines),
                type: recipeType.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty,
                category: category.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty,
                description: description.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty,
                ingredients: cleanIngredients,
                steps: cleanSteps,
                servings: cleanServings,
                verified: verified && !cleanIngredients.isEmpty
            )
            guard result.ok != false else {
                errorMessage = result.message ?? "Der Import konnte nicht gespeichert werden."
                return
            }
            await onChanged()
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func discard() async {
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }
        do {
            _ = try await session.api.resolvePending(url: item.url, action: "skip")
            await onChanged()
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func parsedServings() throws -> Int? {
        let value = servings.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return nil }
        guard let number = Int(value), (1...50).contains(number) else {
            throw PendingValidationError.invalidServings
        }
        return number
    }

    private func parsedIngredients() throws -> [PendingIngredient] {
        try ingredients.compactMap { draft in
            let cleanName = draft.name.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !cleanName.isEmpty else { return nil }
            let amountText = draft.amount
                .trimmingCharacters(in: .whitespacesAndNewlines)
                .replacingOccurrences(of: ",", with: ".")
            let amount: Double?
            if amountText.isEmpty {
                amount = nil
            } else if let parsed = Double(amountText), parsed >= 0 {
                amount = parsed
            } else {
                throw PendingValidationError.invalidAmount(cleanName)
            }
            return PendingIngredient(
                name: cleanName,
                amount: amount,
                unit: draft.unit.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty,
                raw: draft.raw?.nilIfEmpty
            )
        }
    }

    private func parsedSteps() throws -> [PendingStep] {
        try steps.compactMap { draft in
            let instruction = draft.instruction.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !instruction.isEmpty else { return nil }
            let timerText = draft.timerSeconds.trimmingCharacters(in: .whitespacesAndNewlines)
            let seconds: Int?
            if timerText.isEmpty {
                seconds = nil
            } else if let parsed = Int(timerText), (1...86_400).contains(parsed) {
                seconds = parsed
            } else {
                throw PendingValidationError.invalidTimer
            }
            return PendingStep(instruction: instruction, timerSeconds: seconds)
        }
    }

    private func safeExternalURL(_ raw: String) -> URL? {
        guard let url = URL(string: raw),
              ["https", "http"].contains(url.scheme?.lowercased()) else {
            return nil
        }
        return url
    }

    private static func ingredientDrafts(from suggestion: PendingSuggestion?) -> [PendingIngredientDraft] {
        guard let values = suggestion?.ingredients, !values.isEmpty else {
            return [PendingIngredientDraft()]
        }
        return values.map(PendingIngredientDraft.init)
    }

    private static func stepDrafts(from suggestion: PendingSuggestion?) -> [PendingStepDraft] {
        guard let values = suggestion?.steps, !values.isEmpty else {
            return [PendingStepDraft()]
        }
        return values.map(PendingStepDraft.init)
    }
}

private struct PendingIngredientDraft: Identifiable {
    let id = UUID()
    var name = ""
    var amount = ""
    var unit = ""
    var raw: String?

    init() {}

    init(_ ingredient: PendingIngredient) {
        name = ingredient.name
        amount = ingredient.amount.map { String($0) } ?? ""
        unit = ingredient.unit ?? ""
        raw = ingredient.raw
    }
}

private struct PendingStepDraft {
    var instruction = ""
    var timerSeconds = ""

    init() {}

    init(_ step: PendingStep) {
        instruction = step.instruction
        timerSeconds = step.timerSeconds.map(String.init) ?? ""
    }
}

private enum PendingValidationError: LocalizedError {
    case invalidPhoto
    case invalidServings
    case invalidAmount(String)
    case invalidTimer

    var errorDescription: String? {
        switch self {
        case .invalidPhoto:
            "Das Foto konnte nicht gelesen werden."
        case .invalidServings:
            "Portionen müssen als ganze Zahl zwischen 1 und 50 angegeben werden."
        case let .invalidAmount(name):
            "Die Menge für \(name) ist keine gültige Zahl."
        case .invalidTimer:
            "Timer müssen zwischen 1 Sekunde und 24 Stunden liegen."
        }
    }
}

private extension String {
    var deletingPathExtension: String {
        (self as NSString).deletingPathExtension
    }
}
