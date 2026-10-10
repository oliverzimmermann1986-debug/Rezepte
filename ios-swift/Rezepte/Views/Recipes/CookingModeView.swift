import SwiftUI
import UIKit

@MainActor
struct CookingModeView: View {
    let recipe: Recipe

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.recipeTheme) private var theme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var voice = CookingVoiceController()
    @State private var completionHistoryID: Int?
    @State private var showCompletionNotes = false
    @State private var completedSteps: Set<Int> = []
    @State private var activeStep = 0
    @State private var servings: Int
    @State private var isLoading = true
    @State private var isSaving = false
    @State private var isFinishing = false
    @State private var showIngredients = true
    @State private var showResetConfirmation = false
    @State private var showCompletion = false
    @State private var hasStartedCooking = false
    @State private var saveState: CookingSaveState = .idle
    @State private var warningMessage: String?
    @State private var errorMessage: String?
    @State private var active = false
    @State private var finished = false

    init(recipe: Recipe) {
        self.recipe = recipe
        _servings = State(initialValue: recipe.servings ?? 1)
    }

    private var originalServings: Int { recipe.servings ?? servings }
    private var multiplier: Double { Double(servings) / Double(max(1, originalServings)) }
    private var canScale: Bool { recipe.servings != nil }
    private var currentStep: RecipeStep? {
        recipe.steps.indices.contains(activeStep) ? recipe.steps[activeStep] : nil
    }
    private var allDone: Bool {
        !recipe.steps.isEmpty && completedSteps.count == recipe.steps.count
    }

    var body: some View {
        Group {
            if isLoading {
                ProgressView("Kochmodus wird vorbereitet …")
            } else if recipe.steps.isEmpty {
                ErrorState(message: "Dieses Rezept hat noch keine Zubereitungsschritte.") {
                    dismiss()
                }
            } else if hasStartedCooking {
                cookingContent
            } else {
                cookingStartContent
            }
        }
        .navigationTitle(recipe.name)
        .navigationBarTitleDisplayMode(.inline)
        .task(id: session.identity) { active = true; await loadProgress() }
        .onAppear { voice.onCommand = handleVoiceCommand }
        .onDisappear { active = false; voice.stop(); voice.onCommand = nil }
        .onChange(of: scenePhase) { _, phase in
            if phase != .active { voice.stop() }
            else { KitchenTimerStore.shared.refresh() }
        }
        .onChange(of: activeStep) { _, _ in if voice.reading { voice.read(currentStep?.instruction ?? "") } }
        .sheet(isPresented: $showCompletionNotes) {
            NavigationStack {
                ScrollView { CookingNotesView(recipeID: recipe.id, editHistoryID: completionHistoryID).padding() }
                    .navigationTitle("So war das Kochen")
                    .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Fertig") { showCompletionNotes = false; dismiss() } } }
            }
        }
        .confirmationDialog(
            "Kochfortschritt zurücksetzen?",
            isPresented: $showResetConfirmation,
            titleVisibility: .visible
        ) {
            Button("Fortschritt löschen", role: .destructive) {
                Task { await resetProgress() }
            }
            Button("Abbrechen", role: .cancel) {}
        } message: {
            Text("Abgehakte Schritte werden gelöscht. Die Kochhistorie bleibt erhalten.")
        }
        .alert("Guten Appetit!", isPresented: $showCompletion) {
            if session.supports("cooking-notes-photos") {
                Button("Notiz oder Foto ergänzen") { showCompletionNotes = true }
            }
            Button("Fertig") { dismiss() }
        } message: {
            Text(
                canScale
                    ? "\(recipe.name) wurde für \(servings) Portionen als gekocht gespeichert."
                    : "\(recipe.name) wurde als gekocht gespeichert."
            )
        }
    }

    private var cookingStartContent: some View {
        ScrollView {
            VStack(spacing: 24) {
                Image(systemName: "fork.knife.circle.fill")
                    .font(.system(size: 58))
                    .foregroundStyle(theme.accent)
                    .accessibilityHidden(true)

                VStack(spacing: 8) {
                    Text(canScale ? "Für wie viele Portionen kochst du?" : "Bereit zum Kochen?")
                        .font(.title2.bold())
                        .multilineTextAlignment(.center)
                    Text(
                        canScale
                            ? "Die Zutatenmengen werden vor dem Start automatisch angepasst."
                            : "Die Portionszahl fehlt. Du kannst trotzdem kochen; die Zutaten bleiben in Originalmenge."
                    )
                        .font(.callout)
                        .foregroundStyle(theme.muted)
                        .multilineTextAlignment(.center)
                }

                if canScale {
                    ServingPicker(
                        value: $servings,
                        original: originalServings,
                        disabled: isSaving
                    )
                }

                if let warningMessage {
                    Label(warningMessage, systemImage: "exclamationmark.triangle")
                        .font(.callout)
                        .foregroundStyle(theme.warning)
                        .cardSurface()
                }

                if let errorMessage {
                    Text(errorMessage)
                        .foregroundStyle(theme.danger)
                        .cardSurface()
                        .accessibilityLabel("Fehler: \(errorMessage)")
                }

                Button {
                    Task { await startCooking() }
                } label: {
                    Label(
                        isSaving ? "Wird vorbereitet …" : "Kochen starten",
                        systemImage: "play.fill"
                    )
                    .frame(maxWidth: .infinity, minHeight: 50)
                }
                .buttonStyle(.borderedProminent)
                .tint(theme.accent)
                .foregroundStyle(theme.ink)
                .disabled(isSaving)
            }
            .frame(maxWidth: 520)
            .padding(24)
            .frame(maxWidth: .infinity)
        }
        .background(theme.background)
    }

    private var cookingContent: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 18) {
                progressHeader
                voiceControls
                if session.supports("cooking-notes-photos") { CookingNotesView(recipeID: recipe.id, compact: true) }

                if let warningMessage {
                    Label(warningMessage, systemImage: "exclamationmark.triangle")
                        .font(.callout)
                        .foregroundStyle(theme.warning)
                        .cardSurface()
                }

                if canScale {
                    servingSelector
                }

                if let step = currentStep {
                    currentStepCard(step)
                }

                stepNavigation

                if let errorMessage {
                    Text(errorMessage)
                        .foregroundStyle(theme.danger)
                        .cardSurface()
                        .accessibilityLabel("Fehler: \(errorMessage)")
                }

                ingredientsSection
                allStepsSection
                finishSection
            }
            .padding()
            .padding(.bottom, 40)
        }
        .background(theme.background)
    }

    private var progressHeader: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Schritt \(activeStep + 1) von \(recipe.steps.count)")
                        .font(.title2.bold())
                    Text(saveLabel)
                        .font(.caption)
                        .foregroundStyle(saveState == .error ? theme.danger : theme.muted)
                        .accessibilityLabel("Speicherstatus: \(saveLabel)")
                }
                Spacer()
                Button("Neu starten", role: .destructive) {
                    showResetConfirmation = true
                }
                .disabled(isSaving || isFinishing)
            }

            ProgressView(value: Double(completedSteps.count), total: Double(recipe.steps.count))
                .tint(theme.success)
                .accessibilityLabel("Kochfortschritt")
                .accessibilityValue("\(completedSteps.count) von \(recipe.steps.count) Schritten")
        }
    }

    private var servingSelector: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("Portionen")
                    .font(.headline)
                Text(servings == originalServings ? "Originalmenge" : "Zutaten werden skaliert")
                    .font(.caption)
                    .foregroundStyle(theme.muted)
            }
            Spacer()
            Button {
                Task { await changeServings(to: servings - 1) }
            } label: {
                Image(systemName: "minus")
                    .frame(width: 42, height: 42)
            }
            .buttonStyle(.bordered)
            .disabled(servings <= 1 || isSaving || isFinishing)

            Text("\(servings)")
                .font(.title3.bold().monospacedDigit())
                .frame(minWidth: 34)

            Button {
                Task { await changeServings(to: servings + 1) }
            } label: {
                Image(systemName: "plus")
                    .frame(width: 42, height: 42)
            }
            .buttonStyle(.bordered)
            .disabled(servings >= 50 || isSaving || isFinishing)
        }
        .cardSurface()
    }

    private func currentStepCard(_ step: RecipeStep) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("SCHRITT \(activeStep + 1)")
                .font(.caption.bold())
                .tracking(1.1)
                .foregroundStyle(theme.muted)
            Text(step.instruction)
                .font(.title3.weight(.semibold))
                .lineSpacing(4)

            if let seconds = step.timerSeconds, seconds > 0 {
                KitchenTimerView(
                    identity: KitchenTimerEntry.identifier(recipeID: recipe.id, stepID: step.id, index: activeStep),
                    seconds: seconds,
                    label: "\(recipe.name), Schritt \(activeStep + 1)"
                )
            }

            Button {
                Task { await toggleCurrentStep() }
            } label: {
                Label(
                    completedSteps.contains(activeStep)
                        ? "Schritt wieder öffnen"
                        : activeStep == recipe.steps.count - 1 ? "Letzten Schritt erledigen" : "Erledigt und weiter",
                    systemImage: completedSteps.contains(activeStep) ? "checkmark.circle.fill" : "circle"
                )
                .frame(maxWidth: .infinity, minHeight: 48)
            }
            .buttonStyle(.borderedProminent)
            .tint(completedSteps.contains(activeStep) ? theme.success : theme.accent)
            .foregroundStyle(completedSteps.contains(activeStep) ? Color.white : theme.ink)
            .disabled(isSaving || isFinishing)
        }
        .padding(20)
        .background(theme.accentSoft, in: RoundedRectangle(cornerRadius: 24))
        .overlay {
            RoundedRectangle(cornerRadius: 24)
                .stroke(theme.accentPressed.opacity(0.45))
        }
    }

    private var stepNavigation: some View {
        HStack(spacing: 12) {
            Button {
                Task { await selectStep(activeStep - 1) }
            } label: {
                Label("Zurück", systemImage: "chevron.left")
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.bordered)
            .disabled(activeStep == 0 || isSaving || isFinishing)

            Button {
                Task { await selectStep(activeStep + 1) }
            } label: {
                Label("Weiter", systemImage: "chevron.right")
                    .labelStyle(.titleAndIcon)
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.bordered)
            .disabled(activeStep == recipe.steps.count - 1 || isSaving || isFinishing)
        }
    }

    private var ingredientsSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button {
                animate { showIngredients.toggle() }
            } label: {
                HStack {
                    Text("Zutaten")
                        .font(.title2.bold())
                    Spacer()
                    Text(showIngredients ? "Ausblenden" : "Anzeigen")
                        .font(.callout.bold())
                    Image(systemName: showIngredients ? "chevron.up" : "chevron.down")
                }
                .foregroundStyle(theme.ink)
                .frame(minHeight: 44)
            }

            if showIngredients {
                VStack(spacing: 0) {
                    ForEach(Array(recipe.ingredients.enumerated()), id: \.offset) { index, ingredient in
                        HStack(alignment: .firstTextBaseline, spacing: 12) {
                            Text(scaledAmount(ingredient))
                                .font(.callout.monospacedDigit())
                                .foregroundStyle(theme.muted)
                                .frame(width: 88, alignment: .leading)
                            Text(ingredient.name)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .padding(.vertical, 11)
                        if index < recipe.ingredients.count - 1 { Divider() }
                    }
                }
                .cardSurface()
                .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
    }

    private var allStepsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Alle Schritte")
                .font(.title2.bold())
            ForEach(Array(recipe.steps.enumerated()), id: \.offset) { index, step in
                Button {
                    Task { await selectStep(index) }
                } label: {
                    HStack(alignment: .top, spacing: 12) {
                        Image(systemName: completedSteps.contains(index) ? "checkmark.circle.fill" : "circle")
                            .foregroundStyle(completedSteps.contains(index) ? theme.success : theme.muted)
                        Text("\(index + 1). \(step.instruction)")
                            .strikethrough(completedSteps.contains(index))
                            .foregroundStyle(completedSteps.contains(index) ? theme.muted : theme.ink)
                            .lineLimit(3)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .padding(14)
                    .background(
                        index == activeStep ? theme.accentSoft : theme.surface,
                        in: RoundedRectangle(cornerRadius: 16)
                    )
                    .overlay {
                        RoundedRectangle(cornerRadius: 16)
                            .stroke(index == activeStep ? theme.accentPressed : theme.outline)
                    }
                }
                .buttonStyle(.plain)
                .disabled(isSaving || isFinishing)
            }
        }
    }

    @ViewBuilder
    private var finishSection: some View {
        if finished {
            Label("Kochen wurde gespeichert", systemImage: "checkmark.seal.fill")
                .foregroundStyle(theme.success).cardSurface()
        } else if allDone {
            VStack(alignment: .leading, spacing: 10) {
                Label("Alles erledigt", systemImage: "checkmark.seal.fill")
                    .font(.title3.bold())
                    .foregroundStyle(theme.success)
                Text("Der Abschluss trägt dieses Kochen in die Historie ein und setzt den Fortschritt zurück.")
                    .font(.callout)
                    .foregroundStyle(theme.muted)
                Button {
                    Task { await finishCooking() }
                } label: {
                    Text(isFinishing ? "Wird abgeschlossen …" : "Kochen abschließen")
                        .frame(maxWidth: .infinity, minHeight: 48)
                }
                .buttonStyle(.borderedProminent)
                .tint(theme.success)
                .disabled(isSaving || isFinishing)
            }
            .cardSurface()
        } else {
            Text("Hake alle Schritte ab, um das Kochen in der Historie zu speichern.")
                .font(.callout)
                .foregroundStyle(theme.muted)
                .frame(maxWidth: .infinity)
        }
    }

    private var saveLabel: String {
        switch saveState {
        case .idle: "\(completedSteps.count) erledigt"
        case .saving: "\(completedSteps.count) erledigt · wird gespeichert …"
        case .saved: "\(completedSteps.count) erledigt · gespeichert"
        case .error: "\(completedSteps.count) erledigt · nicht gespeichert"
        }
    }

    private func loadProgress() async {
        let identity = session.identity
        defer { if session.identity == identity { isLoading = false } }
        guard !recipe.steps.isEmpty else { return }
        do {
            let progress = try await session.api.cookingProgress(id: recipe.id)
            guard session.identity == identity, active, !Task.isCancelled else { return }
            apply(progress)
            hasStartedCooking = progress.exists
        } catch {
            guard session.identity == identity, active, !Task.isCancelled else { return }
            warningMessage = "Der bisherige Fortschritt ist gerade nicht erreichbar. Du kannst neu beginnen."
        }
    }

    private func startCooking() async {
        let didStart = await persist(
            completed: completedSteps,
            active: activeStep,
            servings: servings
        )
        guard didStart else { return }
        animate { hasStartedCooking = true }
    }

    private func toggleCurrentStep() async {
        var nextCompleted = completedSteps
        let wasDone = nextCompleted.contains(activeStep)
        if wasDone {
            nextCompleted.remove(activeStep)
        } else {
            nextCompleted.insert(activeStep)
        }
        let nextStep = !wasDone && activeStep < recipe.steps.count - 1 ? activeStep + 1 : activeStep
        await persist(completed: nextCompleted, active: nextStep, servings: servings)
    }

    private func selectStep(_ index: Int) async {
        guard recipe.steps.indices.contains(index) else { return }
        await persist(completed: completedSteps, active: index, servings: servings)
    }

    private func changeServings(to value: Int) async {
        guard (1...50).contains(value) else { return }
        await persist(completed: completedSteps, active: activeStep, servings: value)
    }

    @discardableResult
    private func persist(completed: Set<Int>, active: Int, servings: Int) async -> Bool {
        guard !isSaving && !isFinishing, !finished, self.active, !session.readOnly else { return false }
        let identity = session.identity
        isSaving = true
        saveState = .saving
        errorMessage = nil
        defer { if identity == session.identity { isSaving = false } }
        do {
            let progress = try await session.api.updateCookingProgress(
                id: recipe.id,
                completedSteps: completed.sorted(),
                activeStep: active,
                servings: servings
            )
            guard identity == session.identity, self.active, !Task.isCancelled else { return false }
            animate { apply(progress) }
            saveState = .saved
            return true
        } catch {
            guard identity == session.identity, self.active, !Task.isCancelled else { return false }
            saveState = .error
            errorMessage = error.localizedDescription
            session.handle(error)
            return false
        }
    }

    private func resetProgress() async {
        guard !isSaving && !isFinishing, active, !session.readOnly else { return }
        let identity = session.identity
        isSaving = true
        saveState = .saving
        errorMessage = nil
        defer { if identity == session.identity { isSaving = false } }
        do {
            _ = try await session.api.clearCookingProgress(id: recipe.id)
            guard identity == session.identity, active, !Task.isCancelled else { return }
            animate {
                completedSteps = []
                activeStep = 0
                servings = originalServings
                hasStartedCooking = false
                finished = false
            }
            UserDefaults.standard.removeObject(forKey: completionStorageKey)
            saveState = .saved
        } catch {
            guard identity == session.identity, active, !Task.isCancelled else { return }
            saveState = .error
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func finishCooking() async {
        guard allDone, !isSaving && !isFinishing, !finished, active, !session.readOnly else { return }
        let identity = session.identity
        let storageKey = completionStorageKey
        isFinishing = true
        errorMessage = nil
        defer { if identity == session.identity { isFinishing = false } }
        let key = completionRequestID()
        do {
            let result = try await session.api.completeCooking(
                id: recipe.id,
                servings: servings,
                idempotencyKey: key
            )
            guard identity == session.identity, active, !Task.isCancelled else { return }
            completionHistoryID = result.completedHistoryID
            finished = true
            voice.stop()
            UserDefaults.standard.removeObject(forKey: storageKey)
            showCompletion = true
        } catch {
            guard identity == session.identity, active, !Task.isCancelled else { return }
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func apply(_ progress: CookingProgress) {
        completedSteps = Set(progress.completedSteps.filter { recipe.steps.indices.contains($0) })
        activeStep = min(max(0, progress.activeStep), max(0, recipe.steps.count - 1))
        servings = min(50, max(1, progress.servings ?? originalServings))
    }

    private var voiceControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Button(voice.listening ? "Sprachsteuerung aus" : "Sprachsteuerung an") {
                    if voice.listening || voice.reading { voice.stop() }
                    else { Task { await voice.start() } }
                }.buttonStyle(.bordered).frame(minHeight: 44)
                Button("Vorlesen") { voice.read(currentStep?.instruction ?? "") }
                    .buttonStyle(.bordered).frame(minHeight: 44)
            }
            Text("Weiter · Zurück · Erledigt · Vorlesen · Timer starten · Timer pausieren").font(.caption).foregroundStyle(theme.muted)
            if let message = voice.message { Text(message).font(.caption).foregroundStyle(theme.warning) }
        }
    }
    private func handleVoiceCommand(_ command: CookingVoiceCommand) {
        guard active, scenePhase == .active, !isSaving, !isFinishing, !finished, !session.readOnly else { return }
        switch command {
        case .next: Task { await selectStep(activeStep + 1) }
        case .previous: Task { await selectStep(activeStep - 1) }
        case .done: Task { await toggleCurrentStep() }
        case .read: voice.read(currentStep?.instruction ?? "")
        case .stop: voice.stop()
        case .startTimer:
            if let seconds = currentStep?.timerSeconds, seconds > 0 {
                let key = KitchenTimerEntry.identifier(recipeID: recipe.id, stepID: currentStep?.id, index: activeStep)
                Task { await KitchenTimerStore.shared.start(id: key, seconds: seconds, label: "\(recipe.name) · Schritt \(activeStep + 1)") }
            }
        case .pauseTimer:
            KitchenTimerStore.shared.pause(id: KitchenTimerEntry.identifier(recipeID: recipe.id, stepID: currentStep?.id, index: activeStep))
        }
    }

    private var completionStorageKey: String {
        "cooking-completion-v1-\(session.username)-\(recipe.id)"
    }

    private func completionRequestID() -> String {
        if let existing = UserDefaults.standard.string(forKey: completionStorageKey),
           !existing.isEmpty,
           existing.count <= 200 {
            return existing
        }
        let created = UUID().uuidString
        UserDefaults.standard.set(created, forKey: completionStorageKey)
        return created
    }

    private func scaledAmount(_ ingredient: Ingredient) -> String {
        let amount = ingredient.amount.map { format($0 * multiplier) } ?? ""
        return [amount, ingredient.unit]
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: " ")
    }

    private func format(_ value: Double) -> String {
        if value.rounded() == value { return String(Int(value)) }
        return value.formatted(.number.precision(.fractionLength(0...2)))
    }

    private func animate(_ update: () -> Void) {
        if reduceMotion {
            update()
        } else {
            withAnimation(.snappy) {
                update()
            }
        }
    }
}

private enum CookingSaveState {
    case idle
    case saving
    case saved
    case error
}
