import SwiftUI

struct MealPlanView: View {
    @EnvironmentObject private var session: SessionStore
    @State private var week: MealWeek?
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var selectedDay: MealDay?
    @State private var cartConfirmation = false

    var body: some View {
        NavigationStack {
            Group {
                if isLoading && week == nil {
                    ProgressView("Wochenplan wird geladen …")
                } else if let errorMessage, week == nil {
                    ErrorState(message: errorMessage) {
                        Task { await load() }
                    }
                } else if let week {
                    ScrollView {
                        LazyVStack(spacing: 14) {
                            weekHeader(week)

                            ForEach(week.days) { day in
                                dayCard(day)
                            }

                            shoppingPreview(week)
                        }
                        .padding()
                    }
                    .refreshable { await load(start: week.weekStart) }
                }
            }
            .background(AppTheme.cream)
            .navigationTitle("Wochenplan")
            .sheet(item: $selectedDay) { day in
                RecipePickerView(day: day) {
                    await load(start: week?.weekStart)
                }
            }
            .overlay(alignment: .bottom) {
                if cartConfirmation {
                    Label("Wocheneinkauf erstellt", systemImage: "checkmark.circle.fill")
                        .padding(.horizontal, 16)
                        .padding(.vertical, 12)
                        .background(.thinMaterial, in: Capsule())
                        .padding(.bottom, 12)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }
            }
            .task { await load() }
        }
    }

    private func weekHeader(_ week: MealWeek) -> some View {
        VStack(spacing: 12) {
            HStack {
                Button {
                    Task { await load(start: week.previousWeek) }
                } label: {
                    Image(systemName: "chevron.left")
                        .frame(width: 44, height: 44)
                }
                .accessibilityLabel("Vorherige Woche")

                Spacer()
                VStack {
                    Text("Woche")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text("\(formatted(week.weekStart)) – \(formatted(week.weekEnd))")
                        .font(.headline)
                }
                Spacer()

                Button {
                    Task { await load(start: week.nextWeek) }
                } label: {
                    Image(systemName: "chevron.right")
                        .frame(width: 44, height: 44)
                }
                .accessibilityLabel("Nächste Woche")
            }

            HStack {
                Label("\(week.summary.plannedMeals) Gerichte", systemImage: "fork.knife")
                Spacer()
                Label("\(week.summary.shoppingItems) Artikel", systemImage: "cart")
            }
            .font(.subheadline)
            .foregroundStyle(.secondary)
        }
        .cardSurface()
    }

    private func dayCard(_ day: MealDay) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(day.label)
                        .font(.headline)
                    Text("\(day.dayNumber).")
                        .font(.caption)
                        .foregroundStyle(day.isToday ? AppTheme.warning : .secondary)
                }
                Spacer()
                Button {
                    selectedDay = day
                } label: {
                    Label("Rezept", systemImage: "plus")
                }
                .buttonStyle(.bordered)
            }

            if day.items.isEmpty {
                Text("Noch nichts geplant")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(day.items) { entry in
                    Divider()
                    HStack(alignment: .center) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(entry.recipeName)
                                .fontWeight(.medium)
                            Text("\(entry.plannedServings) Portionen")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Stepper(
                            "",
                            value: Binding(
                                get: { entry.plannedServings },
                                set: { newValue in
                                    Task { await update(entry, servings: newValue) }
                                }
                            ),
                            in: 1...24
                        )
                        .labelsHidden()
                        Button(role: .destructive) {
                            Task { await delete(entry) }
                        } label: {
                            Image(systemName: "trash")
                        }
                        .accessibilityLabel("\(entry.recipeName) entfernen")
                    }
                }
            }
        }
        .cardSurface()
    }

    private func shoppingPreview(_ week: MealWeek) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Wocheneinkauf")
                    .font(.title2.bold())
                Spacer()
                Button {
                    Task { await createCart(week) }
                } label: {
                    Label("Erstellen", systemImage: "cart.badge.plus")
                }
                .buttonStyle(.borderedProminent)
                .disabled(week.shoppingPreview.isEmpty)
            }

            if week.shoppingPreview.isEmpty {
                Text("Sobald Gerichte geplant sind, erscheint hier die zusammengefasste Einkaufsliste.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(week.shoppingPreview.prefix(8)) { item in
                    Text(previewText(item))
                        .font(.subheadline)
                }
                if week.shoppingPreview.count > 8 {
                    Text("+ \(week.shoppingPreview.count - 8) weitere")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .cardSurface()
    }

    private func load(start: String? = nil) async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            week = try await session.api.mealWeek(start: start)
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func update(_ entry: MealEntry, servings: Int) async {
        do {
            _ = try await session.api.updateMeal(id: entry.id, servings: servings)
            await load(start: week?.weekStart)
        } catch {
            session.handle(error)
        }
    }

    private func delete(_ entry: MealEntry) async {
        do {
            _ = try await session.api.deleteMeal(id: entry.id)
            await load(start: week?.weekStart)
        } catch {
            session.handle(error)
        }
    }

    private func createCart(_ week: MealWeek) async {
        do {
            _ = try await session.api.createWeekCart(start: week.weekStart)
            withAnimation(.snappy) { cartConfirmation = true }
            try? await Task.sleep(for: .seconds(2))
            withAnimation(.snappy) { cartConfirmation = false }
        } catch {
            session.handle(error)
        }
    }

    private func formatted(_ iso: String) -> String {
        guard let date = MealFormatters.dateOnly.date(from: iso) else { return iso }
        return date.formatted(.dateTime.day().month(.abbreviated))
    }

    private func previewText(_ item: ShoppingPreview) -> String {
        let amount = item.amount.map {
            $0.rounded() == $0 ? String(Int($0)) : String(format: "%.2f", $0)
        }
        return [amount, item.unit, item.name].compactMap { $0 }.joined(separator: " ")
    }
}

private enum MealFormatters {
    static let dateOnly: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "de_DE")
        formatter.calendar = Calendar(identifier: .iso8601)
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()
}
