import SwiftUI

struct MealConductorView: View {
    let day: MealDay

    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.recipeTheme) private var theme
    @State private var serveAt = Calendar.current.date(
        bySettingHour: 19,
        minute: 0,
        second: 0,
        of: Date()
    ) ?? Date()
    @State private var burners = 4
    @State private var ovenSlots = 1
    @State private var plan: MealConductorPlan?
    @State private var isLoading = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    configurationCard

                    if isLoading && plan == nil {
                        ProgressView("Ablauf wird berechnet …")
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 36)
                    } else if let errorMessage, plan == nil {
                        ErrorState(message: errorMessage) {
                            Task { await load() }
                        }
                    } else if let plan {
                        summaryCard(plan)

                        ForEach(plan.warnings, id: \.self) { warning in
                            Label(warning, systemImage: "exclamationmark.triangle")
                                .font(.subheadline)
                                .foregroundStyle(theme.warning)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .cardSurface()
                        }

                        Text("Gemeinsamer Ablauf")
                            .font(.title2.bold())

                        ForEach(plan.events) { event in
                            eventCard(event)
                        }
                    }
                }
                .padding()
            }
            .background(theme.background)
            .navigationTitle("Menü-Dirigent")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Schließen") { dismiss() }
                }
            }
            .task { await load() }
        }
    }

    private var configurationCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("\(day.label), \(day.dayNumber).")
                .font(.title3.bold())
            Text("Alle \(day.items.count) Gerichte enden am selben Serviertermin. Der Vorschlag wird nicht gespeichert.")
                .font(.subheadline)
                .foregroundStyle(theme.muted)

            DatePicker("Servieren", selection: $serveAt, displayedComponents: .hourAndMinute)
            Stepper("Herdplatten: \(burners)", value: $burners, in: 1...8)
            Stepper("Ofenplätze: \(ovenSlots)", value: $ovenSlots, in: 1...4)

            Button {
                Task { await load() }
            } label: {
                Label(
                    isLoading ? "Ablauf wird berechnet …" : "Ablauf neu berechnen",
                    systemImage: "wand.and.stars"
                )
                .frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.borderedProminent)
            .tint(theme.accentPressed)
            .disabled(isLoading)
        }
        .cardSurface()
    }

    private func summaryCard(_ plan: MealConductorPlan) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Bereit um \(plan.serveTime)", systemImage: "bell.and.waves.left.and.right.fill")
                .font(.title3.bold())
                .foregroundStyle(theme.success)
            HStack {
                Label("\(plan.summary.recipes) Gerichte", systemImage: "fork.knife")
                Spacer()
                Label("\(plan.summary.steps) Schritte", systemImage: "list.number")
            }
            .font(.subheadline)
            .foregroundStyle(theme.muted)
        }
        .cardSurface()
    }

    private func eventCard(_ event: MealConductorEvent) -> some View {
        HStack(alignment: .top, spacing: 14) {
            VStack(spacing: 3) {
                Text(event.startTime)
                    .font(.headline.monospacedDigit())
                Text(event.endTime)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(theme.muted)
            }
            .frame(width: 54)

            Rectangle()
                .fill(resourceColor(event.resource))
                .frame(width: 3)
                .clipShape(Capsule())

            VStack(alignment: .leading, spacing: 7) {
                HStack {
                    Text(event.recipeName)
                        .font(.subheadline.bold())
                    Spacer()
                    Label(resourceLabel(event.resource), systemImage: resourceIcon(event.resource))
                        .font(.caption)
                        .foregroundStyle(resourceColor(event.resource))
                }
                Text(event.instruction)
                HStack(spacing: 10) {
                    Text("\(event.durationMinutes) Min.")
                    if event.estimated {
                        Label("geschätzt", systemImage: "questionmark.circle")
                    }
                    if event.resourceAdjusted {
                        Label("vorgezogen", systemImage: "arrow.left")
                    }
                }
                .font(.caption)
                .foregroundStyle(theme.muted)
            }
        }
        .cardSurface()
    }

    private func load() async {
        guard !isLoading else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        let components = Calendar.current.dateComponents([.hour, .minute], from: serveAt)
        let serveTime = String(
            format: "%02d:%02d",
            components.hour ?? 19,
            components.minute ?? 0
        )
        do {
            plan = try await session.api.mealConductorPreview(
                date: day.date,
                serveAt: serveTime,
                burners: burners,
                ovenSlots: ovenSlots
            )
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func resourceLabel(_ resource: String) -> String {
        switch resource {
        case "oven": "Ofen"
        case "burner": "Herd"
        default: "Arbeitsfläche"
        }
    }

    private func resourceIcon(_ resource: String) -> String {
        switch resource {
        case "oven": "oven"
        case "burner": "flame"
        default: "hand.raised"
        }
    }

    private func resourceColor(_ resource: String) -> Color {
        switch resource {
        case "oven": theme.warning
        case "burner": theme.danger
        default: theme.accentPressed
        }
    }
}
