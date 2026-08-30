import SwiftUI
import UIKit

struct CookingModeView: View {
    let recipe: Recipe

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.recipeTheme) private var theme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
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

    init(recipe: Recipe) {
        self.recipe = recipe
        _servings = State(initialValue: recipe.servings ?? 1)
    }

    private var originalServings: Int { recipe.servings ?? servings }
    private var multiplier: Double { Double(servings) / Double(max(1, originalServings)) }
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
            } else if recipe.servings == nil {
                ErrorState(message: "Bitte ergänze zuerst die Portionszahl im Rezept, damit Zutaten zuverlässig skaliert werden können.") {
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
        .task { await loadProgress() }
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
            Button("Fertig") { dismiss() }
        } message: {
            Text("\(recipe.name) wurde für \(servings) Portionen als gekocht gespeichert.")
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
                    Text("Für wie viele Portionen kochst du?")
                        .font(.title2.bold())
                        .multilineTextAlignment(.center)
                    Text("Die Zutatenmengen werden vor dem Start automatisch angepasst.")
                        .font(.callout)
                        .foregroundStyle(theme.muted)
                        .multilineTextAlignment(.center)
                }

                ServingPicker(
                    value: $servings,
                    original: originalServings,
                    disabled: isSaving
                )

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

                if let warningMessage {
                    Label(warningMessage, systemImage: "exclamationmark.triangle")
                        .font(.callout)
                        .foregroundStyle(theme.warning)
                        .cardSurface()
                }

                servingSelector

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
                CookingTimerView(
                    identity: "\(recipe.id)-\(step.id ?? activeStep)",
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
        if allDone {
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
        defer { isLoading = false }
        guard !recipe.steps.isEmpty, recipe.servings != nil else { return }
        do {
            let progress = try await session.api.cookingProgress(id: recipe.id)
            apply(progress)
            hasStartedCooking = progress.exists
        } catch {
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
        guard !isSaving && !isFinishing else { return false }
        isSaving = true
        saveState = .saving
        errorMessage = nil
        defer { isSaving = false }
        do {
            let progress = try await session.api.updateCookingProgress(
                id: recipe.id,
                completedSteps: completed.sorted(),
                activeStep: active,
                servings: servings
            )
            animate { apply(progress) }
            saveState = .saved
            return true
        } catch {
            saveState = .error
            errorMessage = error.localizedDescription
            session.handle(error)
            return false
        }
    }

    private func resetProgress() async {
        guard !isSaving && !isFinishing else { return }
        isSaving = true
        saveState = .saving
        errorMessage = nil
        defer { isSaving = false }
        do {
            _ = try await session.api.clearCookingProgress(id: recipe.id)
            animate {
                completedSteps = []
                activeStep = 0
                servings = originalServings
                hasStartedCooking = false
            }
            UserDefaults.standard.removeObject(forKey: completionStorageKey)
            saveState = .saved
        } catch {
            saveState = .error
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func finishCooking() async {
        guard allDone, !isSaving && !isFinishing else { return }
        isFinishing = true
        errorMessage = nil
        defer { isFinishing = false }
        let key = completionRequestID()
        do {
            _ = try await session.api.completeCooking(
                id: recipe.id,
                servings: servings,
                idempotencyKey: key
            )
            UserDefaults.standard.removeObject(forKey: completionStorageKey)
            showCompletion = true
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func apply(_ progress: CookingProgress) {
        completedSteps = Set(progress.completedSteps.filter { recipe.steps.indices.contains($0) })
        activeStep = min(max(0, progress.activeStep), max(0, recipe.steps.count - 1))
        servings = min(50, max(1, progress.servings ?? originalServings))
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

private struct CookingTimerView: View {
    let identity: String
    let seconds: Int
    let label: String

    @Environment(\.recipeTheme) private var theme
    @State private var remaining: Int
    @State private var isRunning = false
    @State private var timerTask: Task<Void, Never>?

    init(identity: String, seconds: Int, label: String) {
        self.identity = identity
        self.seconds = seconds
        self.label = label
        _remaining = State(initialValue: seconds)
    }

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: remaining == 0 ? "bell.fill" : "timer")
                .font(.title2)
                .foregroundStyle(remaining == 0 ? theme.success : theme.ink)
            VStack(alignment: .leading, spacing: 2) {
                Text(remaining == 0 ? "Timer fertig" : formattedTime)
                    .font(.title3.bold().monospacedDigit())
                Text(label)
                    .font(.caption)
                    .foregroundStyle(theme.muted)
                    .lineLimit(1)
            }
            Spacer()
            Button(isRunning ? "Pause" : remaining == 0 ? "Neu" : "Start") {
                isRunning ? pause() : start()
            }
            .buttonStyle(.bordered)
        }
        .padding(14)
        .background(theme.surface, in: RoundedRectangle(cornerRadius: 16))
        .id(identity)
        .onDisappear { timerTask?.cancel() }
    }

    private var formattedTime: String {
        String(format: "%d:%02d", remaining / 60, remaining % 60)
    }

    private func start() {
        if remaining == 0 { remaining = seconds }
        isRunning = true
        timerTask?.cancel()
        timerTask = Task {
            while remaining > 0 && !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                guard !Task.isCancelled else { return }
                remaining -= 1
            }
            if !Task.isCancelled && remaining == 0 {
                isRunning = false
                UINotificationFeedbackGenerator().notificationOccurred(.success)
            }
        }
    }

    private func pause() {
        timerTask?.cancel()
        timerTask = nil
        isRunning = false
    }
}
