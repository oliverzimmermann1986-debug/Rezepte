import SwiftUI

struct CartView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var items: [CartItem] = []
    @State private var suggestions: [ShoppingSuggestion] = []
    @State private var categories: [ShoppingCategory] = []
    @State private var newItem = ""
    @State private var newAmount = ""
    @State private var newUnit = ""
    @State private var selectedCategory = ""
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var addErrorMessage: String?

    private static let commonUnits = [
        "Stück", "g", "kg", "ml", "l", "TL", "EL", "Packung", "Dose", "Glas", "Bund", "Becher"
    ]

    private var openItems: [CartItem] { items.filter { !$0.checked } }
    private var doneItems: [CartItem] { items.filter(\.checked) }
    private var openCategoryNames: [String] { orderedCategories(in: openItems) }

    var body: some View {
        NavigationStack {
            Group {
                if isLoading && items.isEmpty {
                    ProgressView("Einkaufsliste wird geladen …")
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
                    .background(theme.background)
                    .listStyle(.insetGrouped)
                    .refreshable { await load() }
                }
            }
            .navigationTitle("Einkauf")
            .toolbar {
                if !items.isEmpty {
                    ToolbarItem(placement: .topBarTrailing) {
                        Menu {
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
                }
            }
            .task { await load() }
            .task(id: newItem) { await loadSuggestions() }
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
