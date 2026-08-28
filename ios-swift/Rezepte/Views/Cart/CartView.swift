import SwiftUI

struct CartView: View {
    private enum ShoppingMode: String, CaseIterable, Identifiable {
        case current = "Aktuell"
        case recurring = "Wiederkehrend"

        var id: String { rawValue }
    }

    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var mode = ShoppingMode.current
    @State private var items: [CartItem] = []
    @State private var recurringItems: [RecurringCartItem] = []
    @State private var suggestions: [ShoppingSuggestion] = []
    @State private var categories: [ShoppingCategory] = []
    @State private var newItem = ""
    @State private var newAmount = ""
    @State private var newUnit = ""
    @State private var selectedCategory = ""
    @State private var isLoading = true
    @State private var isRecurringLoading = true
    @State private var isRunningRecurring = false
    @State private var errorMessage: String?
    @State private var recurringErrorMessage: String?
    @State private var recurringNotice: String?
    @State private var addErrorMessage: String?
    @State private var recurringEditor: RecurringDraft?
    @State private var recurringToDelete: RecurringCartItem?
    @State private var showShoppingTools = false

    fileprivate static let commonUnits = [
        "Stück", "g", "kg", "ml", "l", "TL", "EL", "Packung", "Dose", "Glas", "Bund", "Becher"
    ]

    private var openItems: [CartItem] { items.filter { !$0.checked } }
    private var doneItems: [CartItem] { items.filter(\.checked) }
    private var openCategoryNames: [String] { orderedCategories(in: openItems) }
    private var dueRecurringCount: Int {
        recurringItems.filter { $0.isActive && $0.dueInDays <= 0 }.count
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                Picker("Einkaufsbereich", selection: $mode) {
                    ForEach(ShoppingMode.allCases) { option in
                        Text(option.rawValue).tag(option)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.horizontal, 16)
                .padding(.vertical, 10)

                if mode == .current {
                    currentContent
                } else {
                    recurringContent
                }
            }
            .background(theme.background)
            .navigationTitle("Einkauf")
            .toolbar {
                if mode == .current, !items.isEmpty {
                    ToolbarItem(placement: .topBarTrailing) {
                        Menu {
                            if session.supports("ai-shopping-optimization") {
                                Button("KI sortieren & exportieren", systemImage: "sparkles") {
                                    showShoppingTools = true
                                }
                            }
                            Button("Erledigte löschen", systemImage: "checkmark.circle") {
                                Task { await clear(onlyChecked: true) }
                            }
                            Button("Liste leeren", systemImage: "trash", role: .destructive) {
                                Task { await clear(onlyChecked: false) }
                            }
                        } label: {
                            Image(systemName: "ellipsis.circle")
                        }
                    }
                } else if mode == .recurring {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button {
                            recurringEditor = RecurringDraft()
                        } label: {
                            Image(systemName: "plus")
                        }
                        .accessibilityLabel("Wiederkehrenden Einkauf anlegen")
                    }
                }
            }
            .sheet(item: $recurringEditor) { draft in
                RecurringEditorView(
                    draft: draft,
                    categories: categories,
                    units: Self.commonUnits
                ) { updatedDraft in
                    try await saveRecurring(updatedDraft)
                }
            }
            .sheet(isPresented: $showShoppingTools) {
                ShoppingToolsView { await load() }
                    .environmentObject(session)
            }
            .confirmationDialog(
                "Wiederholung löschen?",
                isPresented: Binding(
                    get: { recurringToDelete != nil },
                    set: { if !$0 { recurringToDelete = nil } }
                ),
                titleVisibility: .visible
            ) {
                Button("Endgültig löschen", role: .destructive) {
                    guard let item = recurringToDelete else { return }
                    Task { await deleteRecurring(item) }
                }
                Button("Abbrechen", role: .cancel) { recurringToDelete = nil }
            } message: {
                Text("Der Artikel wird künftig nicht mehr automatisch eingetragen.")
            }
            .task {
                await load()
                await loadRecurring()
            }
            .task(id: newItem) { await loadSuggestions() }
        }
    }

    @ViewBuilder
    private var currentContent: some View {
        if isLoading && items.isEmpty {
            ProgressView("Einkaufsliste wird geladen …")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let errorMessage, items.isEmpty {
            ErrorState(message: errorMessage) { Task { await load() } }
        } else {
            List {
                addSection

                if items.isEmpty {
                    Section {
                        EmptyState(
                            icon: "basket",
                            title: "Einkaufsliste leer",
                            message: "Tippe einen Artikel ein oder übernimm die Zutaten eines Rezepts."
                        )
                    }
                } else {
                    ForEach(openCategoryNames, id: \.self) { category in
                        let categoryItems = openItems.filter { categoryName(for: $0) == category }
                        Section {
                            ForEach(categoryItems) { item in
                                cartRow(item)
                            }
                            .onDelete { offsets in delete(offsets, from: categoryItems) }
                        } header: {
                            HStack(spacing: 7) {
                                Text(categoryIcon(category, items: categoryItems))
                                Text(category)
                            }
                        }
                    }

                    if !doneItems.isEmpty {
                        Section("Erledigt · \(doneItems.count)") {
                            ForEach(doneItems) { item in
                                cartRow(item)
                            }
                            .onDelete { offsets in delete(offsets, from: doneItems) }
                        }
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .listStyle(.insetGrouped)
            .refreshable { await load() }
        }
    }

    @ViewBuilder
    private var recurringContent: some View {
        if isRecurringLoading && recurringItems.isEmpty {
            ProgressView("Wiederkehrende Einkäufe werden geladen …")
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let recurringErrorMessage, recurringItems.isEmpty {
            ErrorState(message: recurringErrorMessage) { Task { await loadRecurring() } }
        } else {
            List {
                Section {
                    Button {
                        recurringEditor = RecurringDraft()
                    } label: {
                        Label("Neue Wiederholung", systemImage: "calendar.badge.plus")
                    }

                    if dueRecurringCount > 0 {
                        Button {
                            Task { await runRecurringCart() }
                        } label: {
                            Label(
                                dueRecurringCount == 1
                                    ? "1 fälligen Artikel eintragen"
                                    : "\(dueRecurringCount) fällige Artikel eintragen",
                                systemImage: "arrow.triangle.2.circlepath"
                            )
                        }
                        .disabled(isRunningRecurring)
                    }
                } footer: {
                    Text("Fällige aktive Artikel werden automatisch in dieselbe Einkaufsliste übernommen.")
                }

                if let recurringNotice {
                    Section {
                        Label(recurringNotice, systemImage: "checkmark.circle.fill")
                            .foregroundStyle(theme.success)
                    }
                }

                if recurringItems.isEmpty {
                    Section {
                        EmptyState(
                            icon: "calendar.badge.clock",
                            title: "Noch keine Wiederholungen",
                            message: "Lege regelmäßige Einkäufe wie Milch, Kaffee oder Waschmittel einmalig an."
                        )
                    }
                } else {
                    Section("Deine Wiederholungen") {
                        ForEach(recurringItems) { item in
                            recurringRow(item)
                                .swipeActions(edge: .trailing) {
                                    Button(role: .destructive) {
                                        recurringToDelete = item
                                    } label: {
                                        Label("Löschen", systemImage: "trash")
                                    }
                                    Button {
                                        recurringEditor = RecurringDraft(item: item)
                                    } label: {
                                        Label("Bearbeiten", systemImage: "pencil")
                                    }
                                    .tint(theme.accent)
                                }
                        }
                    }
                }

                if let recurringErrorMessage, !recurringItems.isEmpty {
                    Section {
                        Label(recurringErrorMessage, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(.red)
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .listStyle(.insetGrouped)
            .refreshable { await loadRecurring() }
        }
    }

    private var addSection: some View {
        Section {
            HStack(spacing: 10) {
                TextField("Was fehlt?", text: $newItem)
                    .textInputAutocapitalization(.sentences)
                    .submitLabel(.done)
                    .onSubmit { Task { await add() } }

                Menu {
                    Button("Automatisch zuordnen") { selectedCategory = "" }
                    ForEach(categories) { category in
                        Button("\(category.icon)  \(category.name)") {
                            selectedCategory = category.name
                        }
                    }
                } label: {
                    Image(systemName: selectedCategory.isEmpty ? "square.grid.2x2" : "square.grid.2x2.fill")
                        .frame(width: 32, height: 32)
                }
                .accessibilityLabel("Supermarkt-Kategorie wählen")

                Button {
                    Task { await add() }
                } label: {
                    Image(systemName: "plus.circle.fill")
                        .font(.title2)
                }
                .disabled(newItem.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .accessibilityLabel("Artikel hinzufügen")
            }

            HStack(spacing: 10) {
                TextField("Menge", text: $newAmount)
                    .keyboardType(.decimalPad)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 130)
                    .accessibilityLabel("Menge")

                Menu {
                    Button("Ohne Einheit") { newUnit = "" }
                    ForEach(Self.commonUnits, id: \.self) { unit in
                        Button(unit) { newUnit = unit }
                    }
                } label: {
                    HStack(spacing: 6) {
                        Text(newUnit.nilIfEmpty ?? "Einheit")
                        Image(systemName: "chevron.up.chevron.down")
                            .font(.caption2)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 12)
                    .frame(height: 36)
                    .background(theme.accentSoft, in: RoundedRectangle(cornerRadius: 9))
                }
                .accessibilityLabel("Mengeneinheit wählen")
            }

            if !selectedCategory.isEmpty {
                Label(selectedCategory, systemImage: "building.2.crop.circle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if !suggestions.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(suggestions) { suggestion in
                            Button {
                                select(suggestion)
                            } label: {
                                HStack(spacing: 6) {
                                    Text(suggestion.icon ?? "🛒")
                                    Text(suggestion.name).lineLimit(1)
                                }
                                .font(.subheadline.weight(.medium))
                                .padding(.horizontal, 12)
                                .padding(.vertical, 8)
                                .background(theme.accentSoft, in: Capsule())
                            }
                            .buttonStyle(.plain)
                            .accessibilityHint("Übernimmt Artikel, Einheit und Kategorie in das Formular")
                        }
                    }
                }
            }

            if let addErrorMessage {
                Label(addErrorMessage, systemImage: "exclamationmark.circle.fill")
                    .font(.caption)
                    .foregroundStyle(.red)
            }
        } header: {
            Text("Schnell hinzufügen")
        } footer: {
            Text("Vorschläge kommen aus deinen Rezeptzutaten und bisherigen Einkäufen.")
        }
    }

    private func cartRow(_ item: CartItem) -> some View {
        Button {
            Task { await toggle(item) }
        } label: {
            HStack(spacing: 12) {
                Text(item.icon ?? "🛒")
                    .font(.title3)
                    .frame(width: 30)
                    .saturation(item.checked ? 0 : 1)
                Image(systemName: item.checked ? "checkmark.circle.fill" : "circle")
                    .font(.title3)
                    .foregroundStyle(item.checked ? theme.success : theme.accent)
                Text(item.displayText)
                    .strikethrough(item.checked)
                    .foregroundStyle(item.checked ? Color.secondary : Color.primary)
                Spacer()
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func recurringRow(_ item: RecurringCartItem) -> some View {
        HStack(spacing: 12) {
            Button {
                recurringEditor = RecurringDraft(item: item)
            } label: {
                HStack(spacing: 12) {
                    Text(item.icon ?? "🔁")
                        .font(.title3)
                        .frame(width: 30)
                        .saturation(item.isActive ? 1 : 0)

                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 6) {
                            Text(item.name)
                                .font(.body.weight(.semibold))
                            if let quantityText = item.quantityText {
                                Text("· \(quantityText)")
                                    .foregroundStyle(.secondary)
                            }
                        }
                        Text([item.intervalText, item.category].compactMap { $0?.nilIfEmpty }.joined(separator: " · "))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Text(item.dueText)
                            .font(.caption.weight(.medium))
                            .foregroundStyle(item.isActive && item.dueInDays <= 0 ? theme.accent : Color.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            Toggle(
                "Aktiv",
                isOn: Binding(
                    get: { item.isActive },
                    set: { active in Task { await setRecurring(item, active: active) } }
                )
            )
            .labelsHidden()
            .tint(theme.accent)
        }
    }

    private func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            async let cart = session.api.cart()
            async let catalog = session.api.shoppingCategories()
            let (cartResponse, categoryResponse) = try await (cart, catalog)
            items = cartResponse.items
            categories = categoryResponse.items
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func loadRecurring() async {
        isRecurringLoading = true
        recurringErrorMessage = nil
        defer { isRecurringLoading = false }
        do {
            recurringItems = try await session.api.recurringCart().items
        } catch {
            recurringErrorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func loadSuggestions() async {
        let query = newItem.trimmingCharacters(in: .whitespacesAndNewlines)
        guard query.isEmpty || query.count >= 2 else {
            suggestions = []
            return
        }
        if !query.isEmpty {
            try? await Task.sleep(for: .milliseconds(180))
        }
        guard !Task.isCancelled else { return }
        do {
            suggestions = try await session.api.shoppingSuggestions(
                query: query,
                limit: query.isEmpty ? 12 : 8
            ).items
        } catch {
            suggestions = []
        }
    }

    private func select(_ suggestion: ShoppingSuggestion) {
        newItem = suggestion.name
        newUnit = suggestion.defaultUnit ?? ""
        selectedCategory = suggestion.category ?? ""
        addErrorMessage = nil
        suggestions = []
    }

    private func add() async {
        let name = newItem.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        let amountText = newAmount
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: ",", with: ".")
        let amount: Double?
        if amountText.isEmpty {
            amount = nil
        } else if let parsed = Double(amountText), parsed.isFinite, parsed > 0 {
            amount = parsed
        } else {
            addErrorMessage = "Bitte eine gültige Menge größer als 0 eingeben."
            return
        }
        let unit = amount == nil ? nil : newUnit.nilIfEmpty
        let category = selectedCategory.nilIfEmpty
        do {
            _ = try await session.api.addCartItem(
                name: name,
                amount: amount,
                unit: unit,
                category: category
            )
            newItem = ""
            newAmount = ""
            newUnit = ""
            selectedCategory = ""
            addErrorMessage = nil
            suggestions = []
            await load()
        } catch {
            addErrorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func toggle(_ item: CartItem) async {
        do {
            _ = try await session.api.setCartItem(id: item.id, checked: !item.checked)
            await load()
        } catch {
            session.handle(error)
        }
    }

    private func delete(_ offsets: IndexSet, from visibleItems: [CartItem]) {
        let ids = offsets.map { visibleItems[$0].id }
        Task {
            for id in ids {
                do {
                    _ = try await session.api.deleteCartItem(id: id)
                } catch {
                    session.handle(error)
                }
            }
            await load()
        }
    }

    private func clear(onlyChecked: Bool) async {
        do {
            _ = try await session.api.clearCart(onlyChecked: onlyChecked)
            await load()
        } catch {
            session.handle(error)
        }
    }

    private func saveRecurring(_ draft: RecurringDraft) async throws {
        let name = draft.name.trimmingCharacters(in: .whitespacesAndNewlines)
        let amount = try draft.validatedAmount()
        let unit = amount == nil ? nil : draft.unit.nilIfEmpty
        let category = draft.category.nilIfEmpty
        let nextDueOn = RecurringDraft.backendDateFormatter.string(from: draft.nextDueOn)

        if let id = draft.existingID {
            _ = try await session.api.updateRecurringCartItem(
                id: id,
                name: name,
                amount: amount,
                unit: unit,
                category: category,
                intervalDays: draft.intervalDays,
                nextDueOn: nextDueOn,
                active: draft.active
            )
        } else {
            _ = try await session.api.createRecurringCartItem(
                name: name,
                amount: amount,
                unit: unit,
                category: category,
                intervalDays: draft.intervalDays,
                nextDueOn: nextDueOn,
                active: draft.active
            )
        }
        recurringNotice = draft.existingID == nil ? "Wiederholung angelegt." : "Wiederholung aktualisiert."
        await loadRecurring()
    }

    private func setRecurring(_ item: RecurringCartItem, active: Bool) async {
        if let index = recurringItems.firstIndex(where: { $0.id == item.id }) {
            recurringItems[index].active = active
        }
        do {
            _ = try await session.api.setRecurringCartItem(id: item.id, active: active)
            recurringNotice = active ? "Wiederholung fortgesetzt." : "Wiederholung pausiert."
            await loadRecurring()
        } catch {
            recurringErrorMessage = error.localizedDescription
            session.handle(error)
            await loadRecurring()
        }
    }

    private func deleteRecurring(_ item: RecurringCartItem) async {
        recurringToDelete = nil
        do {
            _ = try await session.api.deleteRecurringCartItem(id: item.id)
            recurringNotice = "Wiederholung gelöscht."
            await loadRecurring()
        } catch {
            recurringErrorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func runRecurringCart() async {
        isRunningRecurring = true
        recurringErrorMessage = nil
        defer { isRunningRecurring = false }
        do {
            let result = try await session.api.runRecurringCart()
            recurringNotice = result.count == 0
                ? "Aktuell ist kein Artikel fällig."
                : "\(result.count) \(result.count == 1 ? "Artikel wurde" : "Artikel wurden") eingetragen."
            await load()
            await loadRecurring()
        } catch {
            recurringErrorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func categoryName(for item: CartItem) -> String {
        item.category?.nilIfEmpty ?? "Sonstiges"
    }

    private func orderedCategories(in source: [CartItem]) -> [String] {
        let present = Set(source.map { categoryName(for: $0) })
        let known = categories.map(\.name).filter(present.contains)
        return known + present.subtracting(known).sorted()
    }

    private func categoryIcon(_ name: String, items: [CartItem]) -> String {
        categories.first(where: { $0.name == name })?.icon
            ?? items.compactMap(\.icon).first
            ?? "🛒"
    }
}

private struct RecurringDraft: Identifiable {
    let id = UUID()
    let existingID: Int?
    var name: String
    var amount: String
    var unit: String
    var category: String
    var intervalDays: Int
    var nextDueOn: Date
    var active: Bool

    static let backendDateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    init(item: RecurringCartItem? = nil) {
        existingID = item?.id
        name = item?.name ?? ""
        if let value = item?.amount {
            amount = value.rounded() == value ? String(Int(value)) : String(format: "%.2f", value)
        } else {
            amount = ""
        }
        unit = item?.defaultUnit ?? ""
        category = item?.category ?? ""
        intervalDays = item?.intervalDays ?? 7
        nextDueOn = item.flatMap { Self.backendDateFormatter.date(from: $0.nextDueOn) } ?? Date()
        active = item?.isActive ?? true
    }

    func validatedAmount() throws -> Double? {
        let text = amount
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: ",", with: ".")
        guard !text.isEmpty else { return nil }
        guard let value = Double(text), value.isFinite, value > 0 else {
            throw RecurringDraftError.invalidAmount
        }
        return value
    }
}

private enum RecurringDraftError: LocalizedError {
    case missingName
    case invalidAmount

    var errorDescription: String? {
        switch self {
        case .missingName: "Bitte einen Artikel eingeben."
        case .invalidAmount: "Bitte eine gültige Menge größer als 0 eingeben."
        }
    }
}

private struct RecurringEditorView: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.recipeTheme) private var theme
    @State private var draft: RecurringDraft
    @State private var isSaving = false
    @State private var errorMessage: String?

    let categories: [ShoppingCategory]
    let units: [String]
    let onSave: (RecurringDraft) async throws -> Void

    init(
        draft: RecurringDraft,
        categories: [ShoppingCategory],
        units: [String],
        onSave: @escaping (RecurringDraft) async throws -> Void
    ) {
        _draft = State(initialValue: draft)
        self.categories = categories
        self.units = units
        self.onSave = onSave
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Artikel") {
                    TextField("Was brauchst du regelmäßig?", text: $draft.name)
                        .textInputAutocapitalization(.sentences)

                    HStack {
                        TextField("Menge", text: $draft.amount)
                            .keyboardType(.decimalPad)
                        Picker("Einheit", selection: $draft.unit) {
                            Text("Ohne Einheit").tag("")
                            ForEach(units, id: \.self) { unit in
                                Text(unit).tag(unit)
                            }
                        }
                    }

                    Picker("Supermarkt-Kategorie", selection: $draft.category) {
                        Text("Automatisch zuordnen").tag("")
                        ForEach(categories) { category in
                            Text("\(category.icon)  \(category.name)").tag(category.name)
                        }
                    }
                }

                Section("Rhythmus") {
                    Stepper(value: $draft.intervalDays, in: 1...3650) {
                        Text(draft.intervalDays == 1 ? "Jeden Tag" : "Alle \(draft.intervalDays) Tage")
                    }
                    DatePicker(
                        "Nächster Einkauf",
                        selection: $draft.nextDueOn,
                        displayedComponents: .date
                    )
                    Toggle("Automatisch eintragen", isOn: $draft.active)
                        .tint(theme.accent)
                }

                if let errorMessage {
                    Section {
                        Label(errorMessage, systemImage: "exclamationmark.circle.fill")
                            .foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle(draft.existingID == nil ? "Neue Wiederholung" : "Wiederholung ändern")
            .navigationBarTitleDisplayMode(.inline)
            .interactiveDismissDisabled(isSaving)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Abbrechen") { dismiss() }
                        .disabled(isSaving)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Speichern") { Task { await save() } }
                        .fontWeight(.semibold)
                        .disabled(isSaving || draft.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
    }

    private func save() async {
        guard !draft.name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            errorMessage = RecurringDraftError.missingName.localizedDescription
            return
        }
        isSaving = true
        errorMessage = nil
        defer { isSaving = false }
        do {
            _ = try draft.validatedAmount()
            try await onSave(draft)
            dismiss()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
