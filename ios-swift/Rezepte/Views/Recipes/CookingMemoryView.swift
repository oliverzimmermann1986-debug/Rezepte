import SwiftUI
import OSLog

struct CookingMemorySection: View {
    let recipe: Recipe
    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var entries: [CookingMemoryEntry] = []
    @State private var pending: [PendingCookingMemory] = []
    @State private var errorMessage: String?
    @State private var isLoading = false
    @State private var showReflection = false
    @State private var deleteEntry: CookingMemoryEntry?
    @State private var showAllEntries = false
    @State private var totalEntries = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Mein Kochgedächtnis", systemImage: "book.closed")
                .font(.title2.bold())
            Text("Was hat funktioniert? Was machst du nächstes Mal anders? Nur für dein Benutzerkonto – das Originalrezept bleibt unverändert.")
                .font(.subheadline)
                .foregroundStyle(theme.muted)

            if let errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(theme.warning)
            }
            if isLoading { ProgressView() }
            if entries.isEmpty && pending.isEmpty && !isLoading {
                Text("Noch keine Erfahrungen gespeichert. Dein erster Hinweis begleitet dich beim nächsten Kochen.")
                    .font(.callout).foregroundStyle(theme.muted)
            }

            ForEach(pending) { item in
                VStack(alignment: .leading, spacing: 7) {
                    Label(item.cancelled ? "Löschung ausstehend" : "Auf diesem Gerät · Synchronisierung ausstehend", systemImage: "arrow.triangle.2.circlepath")
                        .font(.caption.bold()).foregroundStyle(theme.warning)
                    if !item.cancelled {
                        memoryText(note: item.request.note, adjustments: item.request.adjustments, nextTime: item.request.nextTime)
                    }
                    if let message = item.error {
                        Text(message).font(.caption).foregroundStyle(theme.warning)
                    }
                    HStack {
                        Button("Erneut synchronisieren") { Task { await refresh() } }
                            .disabled(isLoading || session.isOffline)
                        Spacer()
                        Menu {
                            Button("Notiz löschen", role: .destructive) {
                                do {
                                    try CookingMemoryStorage.cancel(item, session: session)
                                    readLocal()
                                    Task { await session.syncCookingMemory() }
                                } catch { errorMessage = error.localizedDescription }
                            }.disabled(item.cancelled)
                        } label: { Image(systemName: "ellipsis.circle").frame(minWidth: 44, minHeight: 44) }
                    }
                    Divider()
                }
            }

            ForEach(showAllEntries ? entries : Array(entries.prefix(3))) { entry in
                VStack(alignment: .leading, spacing: 8) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(Date(timeIntervalSince1970: entry.createdAt).formatted(date: .abbreviated, time: .omitted))
                            .font(.caption.bold())
                        if let servings = entry.servings { Text("\(servings) Portionen").font(.caption) }
                        Spacer()
                        Button(role: .destructive) { deleteEntry = entry } label: {
                            Image(systemName: "trash").frame(minWidth: 44, minHeight: 44)
                        }
                        .accessibilityLabel("Kochnotiz löschen")
                        .disabled(session.isOffline)
                    }
                    if let number = entry.stepNumber {
                        Text("Zu Schritt \(number)").font(.caption.bold()).foregroundStyle(theme.accentPressed)
                        if entry.stepIsCurrent == false {
                            Text("Der Schritt wurde inzwischen geändert. Dieser Hinweis gehört zur damaligen Fassung.")
                                .font(.caption).foregroundStyle(theme.warning)
                            if let instruction = entry.stepInstruction {
                                Text(instruction).font(.caption).foregroundStyle(theme.muted)
                            }
                        }
                    }
                    memoryText(note: entry.note, adjustments: entry.adjustments, nextTime: entry.nextTime)
                    Divider()
                }
                .accessibilityElement(children: .contain)
                .accessibilityIdentifier("cookMemorySaved-\(entry.id)")
            }
            if entries.count > 3 {
                Button(showAllEntries ? "Weniger anzeigen" : "Weitere \(entries.count - 3) Erfahrungen anzeigen") { showAllEntries.toggle() }
                    .frame(minHeight: 44)
            }
            if showAllEntries && totalEntries > entries.count && !session.isOffline {
                Button("Ältere Erfahrungen laden") { Task { await loadMore() } }
                    .frame(minHeight: 44).disabled(isLoading)
            }
            Button { showReflection = true } label: {
                Label("Erfahrung festhalten", systemImage: "square.and.pencil")
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.bordered)
            .accessibilityIdentifier("cookMemoryAdd")

            if let history = recipe.cookHistory, !history.isEmpty {
                DisclosureGroup("Kochverlauf im Haushalt") {
                    ForEach(history) { entry in
                        LabeledContent(
                            Date(timeIntervalSince1970: entry.cookedAt).formatted(date: .abbreviated, time: .shortened),
                            value: [entry.cookedBy, entry.servings.map { "\($0) Portionen" }].compactMap { $0 }.joined(separator: " · ")
                        ).font(.caption).padding(.vertical, 4)
                    }
                }
            }
        }
        .cardSurface()
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("cookMemorySection")
        .sheet(isPresented: $showReflection) {
            CookingReflectionView(recipe: recipe, stepNumber: nil) { readLocal() }
        }
        .confirmationDialog("Diese persönliche Kochnotiz löschen?", isPresented: Binding(
            get: { deleteEntry != nil }, set: { if !$0 { deleteEntry = nil } }
        ), titleVisibility: .visible) {
            Button("Notiz löschen", role: .destructive) {
                guard let entry = deleteEntry else { return }
                Task { await delete(entry) }
            }
            Button("Abbrechen", role: .cancel) { deleteEntry = nil }
        }
        .task(id: recipe.id) { await refresh() }
        .onReceive(NotificationCenter.default.publisher(for: .cookingMemoryChanged)) { _ in readLocal() }
    }

    private func memoryText(note: String, adjustments: String, nextTime: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if !note.isEmpty { Text(note) }
            if !adjustments.isEmpty { Text("Angepasst: \(adjustments)") }
            if !nextTime.isEmpty { Label("Nächstes Mal: \(nextTime)", systemImage: "lightbulb") }
        }.font(.subheadline).textSelection(.enabled)
    }

    private func readLocal() {
        do {
            let archive = try CookingMemoryStorage.load(session: session)
            entries = archive.entries[String(recipe.id)] ?? []
            pending = archive.pending.filter { $0.recipeID == recipe.id }
        } catch { errorMessage = error.localizedDescription }
    }

    private func refresh() async {
        guard !isLoading else { return }
        readLocal()
        guard !session.isOffline else { return }
        isLoading = true
        defer { isLoading = false }
        guard let account = session.offlineAccount else { return }
        await session.syncCookingMemory()
        guard session.offlineAccount == account else { return }
        do {
            let generation = try CookingMemoryStorage.load(session: session).generation
            let response = try await session.api.cookingMemory(recipeID: recipe.id, expectedAccount: account)
            guard session.offlineAccount == account else { return }
            try CookingMemoryStorage.update(session: session) { _ = $0.applySnapshot(response.items, recipeID: recipe.id, expectedGeneration: generation) }
            totalEntries = response.total
            errorMessage = nil
            readLocal()
        } catch {
            errorMessage = "Gespeicherte Hinweise bleiben verfügbar. \(error.localizedDescription)"
            if let apiError = error as? APIError, case .unauthenticated = apiError { session.handle(error) }
        }
    }

    private func delete(_ entry: CookingMemoryEntry) async {
        guard let account = session.offlineAccount else { return }
        do {
            try await session.api.deleteCookingMemory(recipeID: recipe.id, entryID: entry.id, expectedAccount: account)
            guard session.offlineAccount == account else { return }
            try CookingMemoryStorage.update(session: session) { $0.entries[String(recipe.id)]?.removeAll { $0.id == entry.id } }
            deleteEntry = nil
            readLocal()
            NotificationCenter.default.post(name: .cookingMemoryChanged, object: recipe.id)
        } catch { errorMessage = error.localizedDescription; session.handle(error) }
    }

    private func loadMore() async {
        guard !isLoading, let account = session.offlineAccount else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let generation = try CookingMemoryStorage.load(session: session).generation
            let response = try await session.api.cookingMemory(recipeID: recipe.id, expectedAccount: account, offset: entries.count)
            guard session.offlineAccount == account else { return }
            try CookingMemoryStorage.update(session: session) { archive in
                guard archive.generation == generation else { return }
                var combined = archive.entries[String(recipe.id)] ?? []
                let existingIDs = Set(combined.map(\.id))
                combined.append(contentsOf: response.items.filter { !existingIDs.contains($0.id) })
                archive.entries[String(recipe.id)] = combined
            }
            totalEntries = response.total
            readLocal()
        } catch { errorMessage = error.localizedDescription; session.handle(error) }
    }
}

struct CookingReflectionView: View {
    private enum Field: String, Hashable {
        case note, adjustments, nextTime
    }

    let recipeID: Int
    let recipeName: String
    let servings: Int?
    let stepNumber: Int?
    let stepInstruction: String?
    let onSaved: () -> Void
    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @State private var draft = CookingMemoryRequest()
    @State private var didLoad = false
    @State private var errorMessage: String?
    // Each editor needs its own focus value; sharing Bool.true is ambiguous.
    @FocusState private var focusedField: Field?

    init(recipe: Recipe, stepNumber: Int?, cookedServings: Int? = nil, onSaved: @escaping () -> Void) {
        recipeID = recipe.id
        recipeName = recipe.name
        servings = cookedServings ?? recipe.servings
        self.stepNumber = stepNumber
        stepInstruction = stepNumber.flatMap { recipe.steps.indices.contains($0 - 1) ? recipe.steps[$0 - 1].instruction : nil }
        self.onSaved = onSaved
    }

    init(recipeID: Int, stepNumber: Int, instruction: String) {
        self.recipeID = recipeID
        recipeName = "Schritt \(stepNumber)"
        servings = nil
        self.stepNumber = stepNumber
        stepInstruction = instruction
        onSaved = {}
    }

    private var draftKey: String { CookingMemoryStorage.draftKey(recipeID: recipeID, stepNumber: stepNumber) }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text(recipeName).font(.headline)
                    Text("Persönlich für dein Konto. Deine Notiz verändert weder das Original noch die Hinweise anderer Personen.")
                        .font(.caption).foregroundStyle(.secondary)
                    if let stepInstruction { Text(stepInstruction).font(.callout) }
                }
                Section("Wie ist es geworden?") {
                    TextField("Geschmack, Konsistenz, Ergebnis …", text: $draft.note, axis: .vertical)
                        .lineLimit(3...6).focused($focusedField, equals: .note).accessibilityIdentifier("cookMemoryNote")
                }
                Section("Das habe ich angepasst") {
                    TextField("Zum Beispiel weniger Salz oder länger gebacken", text: $draft.adjustments, axis: .vertical)
                        .lineLimit(2...5).focused($focusedField, equals: .adjustments).accessibilityIdentifier("cookMemoryAdjustments")
                }
                Section("Beim nächsten Mal") {
                    TextField("Das möchte ich mir merken", text: $draft.nextTime, axis: .vertical)
                        .lineLimit(2...5).focused($focusedField, equals: .nextTime).accessibilityIdentifier("cookMemoryNextTime")
                }
                Section {
                    if let errorMessage { Text(errorMessage).foregroundStyle(.red) }
                    if !draft.isValid && draft.hasContent {
                        Text("Jedes Feld darf höchstens 2.000 Zeichen enthalten.").foregroundStyle(.red)
                    }
                    Label("Zuerst sicher auf diesem Gerät, bei Verbindung mit deinem Server synchronisiert. Abmelden entfernt lokale Daten einschließlich ungesendeter Notizen.", systemImage: "internaldrive")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("Erfahrung speichern") { save() }
                        .frame(minHeight: 44).disabled(!draft.isValid || !didLoad || session.readOnly)
                        .accessibilityIdentifier("cookMemorySave")
                }
            }
            .scrollDismissesKeyboard(.interactively)
            .navigationTitle("Kochgedächtnis")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Schließen") { close() }.accessibilityIdentifier("cookMemoryCancel")
                }
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Fertig") { focusedField = nil }.accessibilityIdentifier("cookMemoryHideKeyboard")
                }
            }
            .task {
                guard !didLoad else { return }
                do {
                    let saved = try CookingMemoryStorage.load(session: session).drafts[draftKey]
                    draft = saved ?? CookingMemoryRequest(servings: servings, stepNumber: stepNumber, stepInstruction: stepInstruction)
                    didLoad = true
                } catch { errorMessage = error.localizedDescription }
            }
            .onChange(of: draft) { _, value in
                guard didLoad else { return }
                do { try CookingMemoryStorage.update(session: session) { $0.drafts[draftKey] = value } }
                catch { errorMessage = "Entwurf nicht gesichert: \(error.localizedDescription)" }
            }
            .onChange(of: focusedField) { _, value in
                traceLifecycle("focus: \(value?.rawValue ?? "none")")
            }
        }
        .onDisappear { traceLifecycle("disappeared") }
    }

    private func close() {
        traceLifecycle("dismiss requested; focus: \(focusedField?.rawValue ?? "none")")
        dismiss()
        traceLifecycle("dismiss returned")
    }

    private func traceLifecycle(_ event: String) {
        #if DEBUG
        // Lifecycle only: never include personal notes, account data or tokens.
        Logger(subsystem: "Rezepte", category: "CookingReflection").notice("\(event, privacy: .public)")
        #endif
    }

    private func save() {
        do {
            try CookingMemoryStorage.enqueue(recipeID: recipeID, request: draft, session: session)
            onSaved()
            if !session.isOffline { Task { await session.syncCookingMemory() } }
            dismiss()
        } catch { errorMessage = error.localizedDescription }
    }
}

struct CookingMemoryStepTips: View {
    let recipeID: Int
    let stepNumber: Int
    let instruction: String
    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var tips: [CookingMemoryEntry] = []
    @State private var localTips: [PendingCookingMemory] = []
    @State private var showReflection = false

    var body: some View {
        if !session.readOnly && session.supports("cooking-memory-v1") {
            VStack(alignment: .leading, spacing: 10) {
                ForEach(tips) { tip in
                    Label([tip.note, tip.adjustments, tip.nextTime].filter { !$0.isEmpty }.joined(separator: "\n"), systemImage: "lightbulb")
                        .font(.callout).foregroundStyle(theme.ink)
                }
                ForEach(localTips) { tip in
                    Label([tip.request.note, tip.request.adjustments, tip.request.nextTime].filter { !$0.isEmpty }.joined(separator: "\n"), systemImage: "internaldrive")
                        .font(.callout).foregroundStyle(theme.ink)
                }
                Button("Für diesen Schritt merken", systemImage: "square.and.pencil") { showReflection = true }
                    .frame(minHeight: 44)
            }
            .accessibilityIdentifier("cookingStepMemory")
            .sheet(isPresented: $showReflection) {
                CookingReflectionView(recipeID: recipeID, stepNumber: stepNumber, instruction: instruction)
            }
            .task(id: "\(recipeID):\(stepNumber):\(instruction)") { await load() }
            .onReceive(NotificationCenter.default.publisher(for: .cookingMemoryChanged)) { _ in readLocal() }
        }
    }

    private func readLocal() {
        guard let archive = try? CookingMemoryStorage.load(session: session) else { return }
        tips = (archive.entries[String(recipeID)] ?? []).filter { $0.matches(stepNumber: stepNumber, instruction: instruction) }
        localTips = archive.pending.filter {
            !$0.cancelled && $0.recipeID == recipeID && $0.request.stepNumber == stepNumber && $0.request.stepInstruction == instruction
        }
    }

    private func load() async {
        readLocal()
        guard !session.isOffline else { return }
        guard let account = session.offlineAccount else { return }
        do {
            let generation = try CookingMemoryStorage.load(session: session).generation
            let response = try await session.api.cookingMemory(recipeID: recipeID, expectedAccount: account)
            guard session.offlineAccount == account else { return }
            try CookingMemoryStorage.update(session: session) { _ = $0.applySnapshot(response.items, recipeID: recipeID, expectedGeneration: generation) }
            readLocal()
        } catch {
            if let apiError = error as? APIError, case .unauthenticated = apiError { session.handle(error) }
        }
    }
}
