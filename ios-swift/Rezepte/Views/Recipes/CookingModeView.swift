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
    @State private var showReflection = false
    @State private var hasStartedCooking = false
    @State private var hasResumableProgress = false
    @State private var didFinishLocally = false
    @State private var localProgress: LocalCookingProgress
    @State private var saveState: CookingSaveState = .idle
    @State private var warningMessage: String?
    @State private var errorMessage: String?

    init(recipe: Recipe) {
        self.recipe = recipe
        _servings = State(initialValue: recipe.servings ?? 1)
        _localProgress = State(initialValue: LocalCookingProgress(recipe: recipe, servings: recipe.servings ?? 1))
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
            if session.supports("cooking-memory-v1") {
                Button("Kochgedächtnis ergänzen") { showReflection = true }
            }
            Button("Fertig") { dismiss() }
        } message: {
            Text("Der Kochabschluss ist auf diesem iPhone gespeichert und wird bei Verbindung einmalig in die Historie übertragen.")
        }
        .sheet(isPresented: $showReflection, onDismiss: { dismiss() }) {
            CookingReflectionView(recipe: recipe, stepNumber: nil, cookedServings: servings, onSaved: {})
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
                    Text(hasResumableProgress ? "Dein Kochen wartet auf dich" : canScale ? "Für wie viele Portionen kochst du?" : "Bereit zum Kochen?")
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
                        isSaving ? "Wird vorbereitet …" : hasResumableProgress ? "Kochen fortsetzen" : "Kochen starten",
                        systemImage: "play.fill"
                    )
                    .frame(maxWidth: .infinity, minHeight: 50)
                }
                .buttonStyle(.borderedProminent)
                .tint(theme.accent)
                .foregroundStyle(theme.ink)
                .disabled(isSaving || session.readOnly)
                .accessibilityIdentifier(hasResumableProgress ? "cookingResume" : "cookingStart")
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

            if !session.readOnly && session.supports("cooking-memory-v1") {
                CookingMemoryStepTips(recipeID: recipe.id, stepNumber: activeStep + 1, instruction: step.instruction)
            }

            if let seconds = step.timerSeconds, seconds > 0 {
                CookingTimerView(
                    identity: timerIdentity(stepIndex: activeStep),
                    seconds: seconds,
                    label: "\(recipe.name), Schritt \(activeStep + 1)"
                )
                .id(timerIdentity(stepIndex: activeStep))
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
            .accessibilityIdentifier("cookingStepNext")
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
                .disabled(isSaving || isFinishing || didFinishLocally)
                .accessibilityIdentifier("cookingComplete")
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
        case .saved: "\(completedSteps.count) erledigt · auf diesem iPhone gespeichert"
        case .error: "\(completedSteps.count) erledigt · nicht gespeichert"
        }
    }

    private func loadProgress() async {
        guard !recipe.steps.isEmpty, !session.readOnly,
              let account = session.offlineAccount else { isLoading = false; return }
        do {
            if let saved = try session.offlineStore.progress(account: account).first(where: { $0.recipeID == recipe.id }) {
                if saved.stepFingerprint == LocalCookingProgress.fingerprint(recipe) {
                    localProgress = saved
                    applyLocal(saved)
                    hasResumableProgress = saved.started
                    saveState = .saved
                    isLoading = false
                    return
                }
                warningMessage = "Die Rezeptschritte haben sich geändert. Dein alter Fortschritt wird nicht auf andere Schritte übertragen."
                isLoading = false
                return
            }
            // The first screen is usable immediately, including when a connection
            // disappears just before the request. Never overwrite new local taps.
            isLoading = false
            guard !session.isOffline else { return }
            let initialRevision = localProgress.revision
            let progress = try await session.api.cookingProgress(id: recipe.id, expectedAccount: account)
            guard account == session.offlineAccount, localProgress.revision == initialRevision,
                  !hasStartedCooking, progress.exists else { return }
            apply(progress)
            localProgress.completedSteps = completedSteps
            localProgress.activeStep = activeStep
            localProgress.servings = servings
            localProgress.started = true
            try session.offlineStore.saveProgress(localProgress, account: account)
            hasResumableProgress = true
        } catch {
            isLoading = false
            if APIError.permitsOfflineFallback(error) {
                warningMessage = "Der Serverfortschritt ist nicht erreichbar. Schritte und Timer werden auf diesem iPhone gespeichert."
            } else { errorMessage = error.localizedDescription }
        }
    }

    private func startCooking() async {
        if let account = session.offlineAccount,
           let old = try? session.offlineStore.progress(account: account).first(where: { $0.recipeID == recipe.id }),
           old.stepFingerprint != localProgress.stepFingerprint {
            CookingTimerNotifications.cancel(account: account, runID: old.runID)
        }
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
        guard !isSaving, !isFinishing, !didFinishLocally, !session.readOnly,
              let account = session.offlineAccount else { return false }
        isSaving = true
        saveState = .saving
        errorMessage = nil
        defer { isSaving = false }
        do {
            var progress = localProgress
            progress.completedSteps = completed
            progress.activeStep = active
            progress.servings = servings
            progress.started = true
            progress.changed()
            try session.offlineStore.saveRecipe(recipe, account: account)
            try session.offlineStore.saveProgress(progress, account: account)
            localProgress = progress
            animate { applyLocal(progress) }
            saveState = .saved
            Task { await session.syncCooking() }
            return true
        } catch {
            saveState = .error
            errorMessage = error.localizedDescription
            return false
        }
    }

    private func resetProgress() async {
        guard !isSaving, !isFinishing, !session.readOnly,
              let account = session.offlineAccount else { return }
        isSaving = true
        saveState = .saving
        errorMessage = nil
        defer { isSaving = false }
        do {
            var reset = LocalCookingProgress(recipe: recipe, servings: originalServings)
            reset.changed()
            try session.offlineStore.saveProgress(reset, account: account)
            cancelTimers(account: account)
            localProgress = reset
            animate {
                completedSteps = []
                activeStep = 0
                servings = originalServings
                hasStartedCooking = false
                hasResumableProgress = false
                didFinishLocally = false
            }
            saveState = .saved
            Task { await session.syncCooking() }
        } catch {
            saveState = .error
            errorMessage = error.localizedDescription
        }
    }

    private func finishCooking() async {
        guard allDone, !isSaving, !isFinishing, !didFinishLocally, !session.readOnly,
              let account = session.offlineAccount else { return }
        isFinishing = true
        errorMessage = nil
        defer { isFinishing = false }
        do {
            try session.offlineStore.finish(localProgress, account: account)
            didFinishLocally = true
            cancelTimers(account: account)
            session.refreshPendingCookingCount()
            showCompletion = true
            Task { await session.syncCooking() }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func apply(_ progress: CookingProgress) {
        completedSteps = Set(progress.completedSteps.filter { recipe.steps.indices.contains($0) })
        activeStep = min(max(0, progress.activeStep), max(0, recipe.steps.count - 1))
        servings = min(50, max(1, progress.servings ?? originalServings))
    }

    private func applyLocal(_ progress: LocalCookingProgress) {
        completedSteps = Set(progress.completedSteps.filter { recipe.steps.indices.contains($0) })
        activeStep = min(max(0, progress.activeStep), max(0, recipe.steps.count - 1))
        servings = min(50, max(1, progress.servings))
    }

    private func timerIdentity(stepIndex: Int) -> String {
        "\(localProgress.runID)-\(recipe.steps[stepIndex].stableID)"
    }

    private func cancelTimers(account: OfflineAccount) {
        CookingTimerNotifications.cancel(account: account, runID: localProgress.runID)
        for index in recipe.steps.indices {
            let identity = timerIdentity(stepIndex: index)
            CookingTimerNotifications.cancel(account: account, identity: identity)
            try? session.offlineStore.remove(key: "timer-\(identity)", account: account)
        }
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
    @EnvironmentObject private var session: SessionStore
    @State private var timer: PersistentCookingTimer
    @State private var errorMessage: String?
    @State private var notificationUnavailable = false

    init(identity: String, seconds: Int, label: String) {
        self.identity = identity
        self.seconds = seconds
        self.label = label
        _timer = State(initialValue: PersistentCookingTimer(deadline: nil, pausedSeconds: seconds))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            TimelineView(.periodic(from: .now, by: 1)) { context in
                let remaining = timer.remaining(at: context.date)
                let running = timer.deadline != nil && remaining > 0
                HStack(spacing: 12) {
                    Image(systemName: remaining == 0 ? "bell.fill" : "timer")
                        .font(.title2)
                        .foregroundStyle(remaining == 0 ? theme.success : theme.ink)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(remaining == 0 ? "Timer fertig" : String(format: "%d:%02d", remaining / 60, remaining % 60))
                            .font(.title3.bold().monospacedDigit())
                        Text(label)
                            .font(.caption)
                            .foregroundStyle(theme.muted)
                            .lineLimit(1)
                    }
                    Spacer()
                    Button(running ? "Pause" : remaining == 0 ? "Neu" : "Start") {
                        updateTimer(pause: running)
                    }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("cookingTimerToggle")
                }
            }
            Text("Die Endzeit bleibt auch beim Verlassen dieses Bildschirms erhalten.")
                .font(.caption2).foregroundStyle(theme.muted)
            if notificationUnavailable {
                Text("Keine Timer-Mitteilung erlaubt. Die Endzeit bleibt gespeichert; prüfe sie in der App.")
                    .font(.caption).foregroundStyle(theme.warning)
            }
            if let errorMessage {
                Text(errorMessage).font(.caption).foregroundStyle(theme.danger)
            }
        }
        .padding(14)
        .background(theme.surface, in: RoundedRectangle(cornerRadius: 16))
        .task {
            guard let account = session.offlineAccount else { return }
            do {
                if let saved = try session.offlineStore.read(PersistentCookingTimer.self, key: "timer-\(identity)", account: account) {
                    timer = saved
                }
            } catch { errorMessage = error.localizedDescription }
        }
    }

    private func updateTimer(pause: Bool) {
        guard let account = session.offlineAccount, !session.readOnly else { return }
        var updated = timer
        if pause { updated.pause() } else { updated.start(duration: seconds) }
        do {
            try session.offlineStore.write(updated, key: "timer-\(identity)", account: account)
            timer = updated
            errorMessage = nil
            if let deadline = updated.deadline {
                Task {
                    let scheduled = await CookingTimerNotifications.schedule(account: account, identity: identity,
                        label: label, deadline: deadline, store: session.offlineStore)
                    if account == session.offlineAccount { notificationUnavailable = !scheduled }
                }
            } else {
                CookingTimerNotifications.cancel(account: account, identity: identity)
            }
        } catch { errorMessage = "Timer konnte nicht gespeichert werden: \(error.localizedDescription)" }
    }
}
