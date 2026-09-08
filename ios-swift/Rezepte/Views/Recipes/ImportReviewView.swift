import SwiftUI

struct ImportIngredientDraft: Codable, Equatable, Identifiable {
    var id = UUID()
    var name: String
    var amount: String
    var unit: String
    var raw: String?
    var wasEdited = false

    var valid: Bool {
        !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && name.count <= 300 && unit.count <= 50
            && (amount.isEmpty || ImportReviewNumber.parse(amount) != nil)
    }
    var payload: ImportReviewIngredient {
        ImportReviewIngredient(name: name, amount: ImportReviewNumber.parse(amount), unit: unit.isEmpty ? nil : unit, raw: wasEdited ? nil : raw)
    }
}

struct ImportStepDraft: Codable, Equatable, Identifiable {
    var id = UUID()
    var instruction: String
    var timerSeconds: String
    var valid: Bool {
        !instruction.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && instruction.count <= 10000
            && (timerSeconds.isEmpty || (Int(timerSeconds).map { (1...86400).contains($0) } ?? false))
    }
    var payload: ImportReviewStep { ImportReviewStep(instruction: instruction, timerSeconds: Int(timerSeconds)) }
}

struct ImportReviewDraft: Codable, Equatable {
    var revision: String
    var ingredients: [ImportIngredientDraft]
    var steps: [ImportStepDraft]
    var servings: String
    var reason = ""
    var requestID = UUID().uuidString

    init(report: ImportReviewResponse) {
        revision = report.revision
        ingredients = report.ingredients.map {
            ImportIngredientDraft(name: $0.name, amount: ImportReviewNumber.format($0.amount), unit: $0.unit ?? "", raw: $0.raw)
        }
        steps = report.steps.map { ImportStepDraft(instruction: $0.instruction, timerSeconds: $0.timerSeconds.map(String.init) ?? "") }
        servings = report.servings.map(String.init) ?? ""
    }

    var valid: Bool {
        !ingredients.isEmpty && !steps.isEmpty && ingredients.count <= 200 && steps.count <= 200
            && ingredients.allSatisfy(\.valid) && steps.allSatisfy(\.valid)
            && (servings.isEmpty || (Int(servings).map { (1...50).contains($0) } ?? false))
            && !reason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && reason.count <= 1000
    }
    var payload: ImportReviewRequest {
        ImportReviewRequest(clientRequestId: requestID, expectedRevision: revision, ingredients: ingredients.map(\.payload),
                            steps: steps.map(\.payload), servings: Int(servings), reason: reason)
    }
}

struct ImportReviewView: View {
    let recipeID: Int
    let recipeName: String
    let onApplied: () async -> Void
    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.recipeTheme) private var theme
    @State private var report: ImportReviewResponse?
    @State private var draft: ImportReviewDraft?
    @State private var page = 0
    @State private var isLoading = false
    @State private var isSaving = false
    @State private var errorMessage: String?
    @State private var successMessage: String?
    @State private var reloadConfirmation = false
    @State private var conflict = false
    @State private var confirmCorrection: ImportCorrection?

    private var key: String { "import-review-draft-\(recipeID)" }

    var body: some View {
        NavigationStack {
            Group {
                if report == nil {
                    if isLoading { ProgressView("Importprüfung wird geladen …") }
                    else {
                        ErrorState(message: errorMessage ?? "Importprüfung noch nicht geladen.") { Task { await load() } }
                    }
                } else {
                    Form {
                        Section {
                            Text(recipeName).font(.headline)
                            Picker("Prüfschritt", selection: $page) {
                                Text("Quelle").tag(0)
                                Text("Zutaten").tag(1)
                                Text("Schritte").tag(2)
                                Text("Abschluss").tag(3)
                            }.pickerStyle(.segmented)
                            if let errorMessage { Label(errorMessage, systemImage: "exclamationmark.triangle").foregroundStyle(theme.danger) }
                            if let successMessage { Label(successMessage, systemImage: "checkmark.circle").foregroundStyle(theme.success) }
                            if conflict {
                                Text("Dein Entwurf bleibt erhalten. Prüfe die aktuelle Rezeptfassung, bevor du Änderungen erneut übernimmst.")
                                    .font(.caption)
                                Button("Aktuelle Fassung neu laden") { reloadConfirmation = true }
                            }
                        }
                        if page == 0 { sourceAndIssues }
                        if page == 1 { ingredientEditor }
                        if page == 2 { stepEditor }
                        if page == 3 { reviewAndSave }
                        if page < 3 {
                            Section {
                                Button(page == 0 ? "Zutaten prüfen" : page == 1 ? "Zubereitung prüfen" : "Änderungen vergleichen") { page += 1 }
                                    .frame(minHeight: 44)
                            }
                        }
                    }
                    .disabled(isSaving)
                }
            }
            .navigationTitle("Import fertigstellen")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Schließen") { dismiss() }.disabled(isSaving).accessibilityIdentifier("importReviewClose")
                }
            }
            .interactiveDismissDisabled(isSaving)
            .task { await load() }
            .confirmationDialog("Entwurf verwerfen und die aktuelle Fassung laden?", isPresented: $reloadConfirmation, titleVisibility: .visible) {
                Button("Entwurf verwerfen", role: .destructive) { Task { await load(discardDraft: true) } }
                Button("Abbrechen", role: .cancel) {}
            }
            .confirmationDialog("Diesen Vorschlag ins gemeinsame Rezept übernehmen?", isPresented: Binding(
                get: { confirmCorrection != nil }, set: { if !$0 { confirmCorrection = nil } }
            ), titleVisibility: .visible) {
                Button("Vorschlag übernehmen") {
                    guard let correction = confirmCorrection else { return }
                    Task { await apply(correction) }
                }
                Button("Abbrechen", role: .cancel) { confirmCorrection = nil }
            }
        }
    }

    private var sourceAndIssues: some View {
        Group {
            Section("Gespeicherte Originalquelle") {
                if let value = report?.source.url, let url = URL(string: value), ["http", "https"].contains(url.scheme?.lowercased() ?? ""), url.host != nil {
                    Link("Original im Browser öffnen", destination: url)
                    Text(value).font(.caption).textSelection(.enabled)
                }
                if let text = report?.source.description, !text.isEmpty {
                    Text(text).font(.callout).textSelection(.enabled)
                } else {
                    Text("Kein Originaltext gespeichert. Ergänze fehlende Angaben nur anhand einer verlässlichen Quelle.").foregroundStyle(theme.warning)
                }
                Text("Dies ist der beim Import gespeicherte Auszug, keine Garantie für die vollständige oder aktuelle Originalseite.")
                    .font(.caption).foregroundStyle(theme.muted)
            }.accessibilityIdentifier("importReviewSource")
            Section("Hinweise zur gespeicherten Fassung") {
                ForEach(report?.quality.issues ?? []) { issue in
                    Button {
                        page = issue.section == "ingredients" || issue.section == "servings" ? 1 : issue.section == "steps" ? 2 : 0
                    } label: {
                        VStack(alignment: .leading, spacing: 5) {
                            Text(issue.title).font(.headline)
                            Text(issue.detail).font(.caption).foregroundStyle(theme.muted)
                        }.frame(minHeight: 44, alignment: .leading)
                    }.buttonStyle(.plain)
                }
                Text("Die Strukturprüfung bestätigt keine Lebensmittel- oder Allergensicherheit. Mengen werden nicht geraten.")
                    .font(.caption).foregroundStyle(theme.warning)
            }.accessibilityIdentifier("importReviewIssues")
        }
    }

    private var ingredientEditor: some View {
        Group {
            Section("Portionen") {
                TextField("Unbekannt", text: field(\.servings)).keyboardType(.numberPad)
                Text("Ohne verlässliche Angabe leer lassen. Skalierung bleibt dann eingeschränkt.").font(.caption)
            }
            if let draft {
                ForEach(Array(draft.ingredients.enumerated()), id: \.element.id) { index, ingredient in
                    Section("Zutat \(index + 1)") {
                        TextField("Zutat", text: ingredientField(index, \.name)).accessibilityIdentifier("importReviewIngredient-\(index)")
                        HStack {
                            TextField("Menge unbekannt", text: ingredientField(index, \.amount)).keyboardType(.decimalPad)
                            TextField("Einheit", text: ingredientField(index, \.unit))
                        }
                        if let raw = ingredient.raw, !raw.isEmpty { Text("Importiert: \(raw)").font(.caption).foregroundStyle(theme.muted) }
                        if !ingredient.valid { Text("Name ergänzen; Menge leer lassen oder eine Zahl von 0 bis 1.000.000 eingeben.").font(.caption).foregroundStyle(theme.danger) }
                        Button("Zutat entfernen", role: .destructive) { edit { $0.ingredients.remove(at: index) } }
                    }
                }
            }
            Section {
                Button("Zutat ergänzen", systemImage: "plus") { edit { $0.ingredients.append(ImportIngredientDraft(name: "", amount: "", unit: "")) } }
                    .disabled((draft?.ingredients.count ?? 0) >= 200)
                sourceReference
            }
        }
    }

    private var stepEditor: some View {
        Group {
            if let draft {
                ForEach(Array(draft.steps.enumerated()), id: \.element.id) { index, step in
                    Section("Schritt \(index + 1)") {
                        TextField("Zubereitung", text: stepField(index, \.instruction), axis: .vertical)
                            .lineLimit(3...10).accessibilityIdentifier("importReviewStep-\(index)")
                        LabeledContent("Timer in Sekunden") {
                            TextField("Keiner", text: stepField(index, \.timerSeconds)).keyboardType(.numberPad).multilineTextAlignment(.trailing)
                        }
                        if !step.valid { Text("Anweisung ergänzen; Timer leer lassen oder 1–86.400 Sekunden angeben.").font(.caption).foregroundStyle(theme.danger) }
                        HStack {
                            Button("Nach oben", systemImage: "arrow.up") { edit { $0.steps.swapAt(index, index - 1) } }.disabled(index == 0)
                            Button("Nach unten", systemImage: "arrow.down") { edit { $0.steps.swapAt(index, index + 1) } }.disabled(index == draft.steps.count - 1)
                            Spacer()
                            Button("Entfernen", role: .destructive) { edit { $0.steps.remove(at: index) } }
                        }.buttonStyle(.borderless).frame(minHeight: 44)
                    }
                }
            }
            Section {
                Button("Schritt ergänzen", systemImage: "plus") { edit { $0.steps.append(ImportStepDraft(instruction: "", timerSeconds: "")) } }
                    .disabled((draft?.steps.count ?? 0) >= 200)
                sourceReference
            }
        }
    }

    private var sourceReference: some View {
        DisclosureGroup("Originaltext daneben prüfen") {
            Text(report?.source.description ?? "Kein Originaltext vorhanden.")
                .font(.callout).textSelection(.enabled)
        }
    }

    private var reviewAndSave: some View {
        Group {
            if let report, let draft {
                Section("Vorher → dein Entwurf") {
                    comparison(title: "Portionen", before: report.servings.map(String.init) ?? "Unbekannt", after: draft.servings.isEmpty ? "Unbekannt" : draft.servings)
                    comparison(title: "Zutaten", before: ingredientsText(report.ingredients), after: ingredientsText(draft.ingredients.map(\.payload)))
                    comparison(title: "Zubereitung", before: stepsText(report.steps), after: stepsText(draft.steps.map(\.payload)))
                    Text("Originalquelle und bisherige Fassung bleiben im Änderungsverlauf erhalten. Der Prüfstatus und berechnete Nährwerte müssen anschließend neu geprüft werden.")
                        .font(.caption).foregroundStyle(theme.muted)
                }
                Section("Was wurde korrigiert?") {
                    TextField("Grund und verwendete Quelle", text: field(\.reason), axis: .vertical).lineLimit(2...6)
                    Text(report.canApply ? "Du übernimmst diese Korrektur ins gemeinsame Rezept." : "Dein Vorschlag wird zur Freigabe gespeichert. Das gemeinsame Rezept bleibt bis dahin unverändert.")
                        .font(.caption)
                    Button(report.canApply ? "Korrektur übernehmen" : "Korrektur vorschlagen") { Task { await save() } }
                        .disabled(!draft.valid || conflict || session.isOffline || isSaving || session.readOnly)
                        .frame(minHeight: 44).accessibilityIdentifier("importReviewSave")
                    if isSaving { ProgressView("Korrektur wird gespeichert …") }
                    if session.isOffline { Text("Dein Entwurf bleibt lokal gespeichert. Zum Einreichen wird eine Verbindung benötigt.").font(.caption) }
                }
                if !report.corrections.isEmpty {
                    Section("Nachvollziehbare Korrekturen") {
                        ForEach(report.corrections) { correction in
                            DisclosureGroup("\(correctionStatus(correction.status)) · \(correction.username.isEmpty ? "Gelöschtes Konto" : correction.username)") {
                                Text(correction.reason ?? "").font(.callout)
                                comparison(title: "Zutaten", before: ingredientsText(correction.before.ingredients), after: ingredientsText(correction.proposed.ingredients))
                                comparison(title: "Zubereitung", before: stepsText(correction.before.steps), after: stepsText(correction.proposed.steps))
                                comparison(title: "Portionen", before: correction.before.servings.map(String.init) ?? "Unbekannt", after: correction.proposed.servings.map(String.init) ?? "Unbekannt")
                                if correction.status == "pending" && report.canApply {
                                    Button("Diesen Vorschlag übernehmen") { confirmCorrection = correction }
                                        .disabled(session.isOffline)
                                }
                                if correction.status == "pending" {
                                    Button(report.canApply ? "Vorschlag verwerfen" : "Vorschlag zurückziehen", role: .destructive) {
                                        Task { await withdraw(correction) }
                                    }.disabled(session.isOffline)
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    private func comparison(title: String, before: String, after: String) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title).font(.headline)
            if before == after { Text("Unverändert").font(.caption).foregroundStyle(theme.muted) }
            else {
                Text("Vorher").font(.caption.bold())
                Text(before.isEmpty ? "Keine Angaben" : before).font(.callout).foregroundStyle(theme.muted)
                Text("Danach").font(.caption.bold())
                Text(after.isEmpty ? "Keine Angaben" : after).font(.callout)
            }
        }.padding(.vertical, 4).textSelection(.enabled)
    }

    private func correctionStatus(_ status: String) -> String {
        switch status {
        case "applied": "Übernommen"
        case "withdrawn": "Zurückgezogen"
        default: "Vorschlag"
        }
    }

    private func ingredientsText(_ ingredients: [ImportReviewIngredient]) -> String {
        ingredients.map {
            $0.raw?.nilIfEmpty ?? [ImportReviewNumber.format($0.amount), $0.unit ?? "", $0.name].filter { !$0.isEmpty }.joined(separator: " ")
        }.joined(separator: "\n")
    }

    private func stepsText(_ steps: [ImportReviewStep]) -> String {
        steps.enumerated().map { "\($0.offset + 1). \($0.element.instruction)\($0.element.timerSeconds.map { " (\($0) s)" } ?? "")" }.joined(separator: "\n")
    }

    private func field(_ path: WritableKeyPath<ImportReviewDraft, String>) -> Binding<String> {
        Binding(get: { draft?[keyPath: path] ?? "" }, set: { value in edit { $0[keyPath: path] = value } })
    }
    private func ingredientField(_ index: Int, _ path: WritableKeyPath<ImportIngredientDraft, String>) -> Binding<String> {
        Binding(get: { draft?.ingredients.indices.contains(index) == true ? draft!.ingredients[index][keyPath: path] : "" }, set: { value in
            edit {
                if $0.ingredients.indices.contains(index) {
                    $0.ingredients[index][keyPath: path] = value
                    $0.ingredients[index].wasEdited = true
                }
            }
        })
    }
    private func stepField(_ index: Int, _ path: WritableKeyPath<ImportStepDraft, String>) -> Binding<String> {
        Binding(get: { draft?.steps.indices.contains(index) == true ? draft!.steps[index][keyPath: path] : "" }, set: { value in
            edit { if $0.steps.indices.contains(index) { $0.steps[index][keyPath: path] = value } }
        })
    }
    private func edit(_ operation: (inout ImportReviewDraft) -> Void) {
        guard var value = draft else { return }
        operation(&value)
        value.requestID = UUID().uuidString
        draft = value
        successMessage = nil
        do {
            guard let account = session.offlineAccount else { throw APIError.unauthenticated }
            try session.offlineStore.write(value, key: key, account: account)
        } catch { errorMessage = "Entwurf nicht lokal gespeichert: \(error.localizedDescription)" }
    }

    private func load(discardDraft: Bool = false) async {
        isLoading = true
        defer { isLoading = false }
        guard let account = session.offlineAccount else { return }
        do {
            let response = try await session.api.importReview(recipeID: recipeID, expectedAccount: account)
            guard session.offlineAccount == account else { return }
            // Remove only after a successful read; failed reconnects must not destroy a draft.
            if discardDraft { try session.offlineStore.remove(key: key, account: account) }
            let saved = try session.offlineStore.read(ImportReviewDraft.self, key: key, account: account)
            report = response
            draft = saved ?? ImportReviewDraft(report: response)
            conflict = saved.map { $0.revision != response.revision } ?? false
            errorMessage = nil
        } catch { errorMessage = error.localizedDescription; session.handle(error) }
    }

    private func save() async {
        guard let draft, draft.valid else { return }
        isSaving = true
        defer { isSaving = false }
        guard let account = session.offlineAccount else { return }
        do {
            let response = try await session.api.saveImportReview(recipeID: recipeID, request: draft.payload, expectedAccount: account)
            guard session.offlineAccount == account else { return }
            try session.offlineStore.remove(key: key, account: account)
            report = response.review
            self.draft = ImportReviewDraft(report: response.review)
            errorMessage = nil
            successMessage = response.status == "applied" ? "Korrektur übernommen. Original und Änderung bleiben nachvollziehbar." : "Vorschlag zur Freigabe gespeichert."
            if response.status == "applied" { await onApplied() }
        } catch {
            if let apiError = error as? APIError, case .server(409, _) = apiError { conflict = true }
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func apply(_ correction: ImportCorrection) async {
        isSaving = true
        defer { isSaving = false }
        guard let account = session.offlineAccount else { return }
        do {
            let response = try await session.api.applyImportCorrection(recipeID: recipeID, correctionID: correction.id, expectedAccount: account)
            guard session.offlineAccount == account else { return }
            report = response.review
            conflict = draft?.revision != response.review.revision
            successMessage = "Vorschlag übernommen."
            confirmCorrection = nil
            await onApplied()
        } catch { errorMessage = error.localizedDescription; session.handle(error) }
    }

    private func withdraw(_ correction: ImportCorrection) async {
        isSaving = true
        defer { isSaving = false }
        guard let account = session.offlineAccount else { return }
        do {
            try await session.api.withdrawImportCorrection(recipeID: recipeID, correctionID: correction.id, expectedAccount: account)
            guard session.offlineAccount == account else { return }
            await load()
            successMessage = "Vorschlag zurückgezogen. Der Verlauf bleibt erhalten."
        } catch { errorMessage = error.localizedDescription; session.handle(error) }
    }
}
