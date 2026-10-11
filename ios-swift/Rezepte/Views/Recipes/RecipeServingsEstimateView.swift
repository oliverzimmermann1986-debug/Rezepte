import SwiftUI

@MainActor
struct RecipeServingsEstimateView: View {
    let target: RecipeServingsEstimateTarget
    @EnvironmentObject private var session: SessionStore
    @Environment(\.dismiss) private var dismiss
    @StateObject private var state = HouseholdFeatureState()
    @StateObject private var aiConsent = AIConsentCoordinator()
    @State private var estimate: RecipeServingsEstimate?
    @State private var selectedServings = 1

    private var available: Bool {
        target.identity == session.identity && !session.readOnly && session.role != .guest && !session.isOfflineSession
            && session.supports("servings-estimate-v1")
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text(target.name).font(.headline)
                    let current = estimate == nil ? target.currentServings : estimate?.currentServings
                    Text(current.map { "Aktuell gespeichert: \($0) Portionen" } ?? "Noch keine Portionsanzahl gespeichert")
                    Text("Die KI prüft anhand der Zutatenmengen, für wie viele Portionen das Rezept plausibel reicht. Portionsgrößen hängen vom Gericht und vom Appetit ab.")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                Section {
                    Button(estimate == nil ? "Mit KI prüfen" : "Erneut mit KI prüfen", systemImage: "sparkles") {
                        Task { await check() }
                    }.disabled(!available || state.busy || state.identity != session.identity)
                    HouseholdFeedback(state: state)
                }
                if let estimate {
                    Section("Ergebnis") {
                        if let suggestion = estimate.suggestedServings {
                            Text("Vorschlag: \(suggestion) Portionen").font(.title3.bold())
                            if let range = estimate.rangeText { Text(range) }
                            Label(estimate.confidence.title, systemImage: estimate.confidence == .low ? "exclamationmark.circle" : "info.circle")
                                .foregroundStyle(estimate.confidence == .low ? Color.orange : Color.secondary)
                        } else {
                            Label("Keine verlässliche Schätzung möglich", systemImage: "questionmark.circle")
                        }
                        Text(estimate.rationale)
                        ForEach(Array(estimate.warnings.enumerated()), id: \.offset) { _, warning in
                            Text(warning).font(.subheadline).foregroundStyle(.secondary)
                        }
                    }
                    if estimate.suggestedServings != nil {
                        Section {
                            Stepper("\(selectedServings) Portionen übernehmen", value: $selectedServings, in: 1...50)
                                .disabled(state.busy)
                            Button("Portionsangabe speichern", systemImage: "checkmark") { Task { await apply() } }
                                .disabled(!available || state.busy)
                        } footer: {
                            Text("Prüfe den Vorschlag und passe ihn bei Bedarf an. Übernommen wird nur die Portionsangabe; die Zutatenmengen werden nicht skaliert.")
                        }
                    }
                }
            }
            .navigationTitle("Portionen prüfen")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Schließen") { dismiss() }.disabled(state.busy) } }
            .interactiveDismissDisabled(state.busy)
            .aiConsentPrompt(aiConsent)
            .task {
                guard target.identity == session.identity else { dismiss(); return }
                state.reset(session)
            }
            .onChange(of: session.identity) { _, _ in aiConsent.cancel(); estimate = nil; dismiss() }
        }
    }

    private func check() async {
        guard available, let token = state.begin(session) else { return }
        defer { state.finish(token, session) }
        guard let consent = await aiConsent.request(.servingsEstimate, session: session), state.current(token, session) else { return }
        estimate = nil
        do {
            let result = try await session.api.estimateRecipeServings(id: target.id, consent: consent)
            guard state.current(token, session) else { return }
            estimate = result
            selectedServings = result.suggestedServings ?? 1
        } catch { state.failed(error, token: token, session: session) }
    }

    private func apply() async {
        guard available, let estimate, estimate.suggestedServings != nil, let token = state.begin(session) else { return }
        defer { state.finish(token, session) }
        do {
            _ = try await session.api.applyRecipeServingsEstimate(id: target.id, servings: selectedServings, fingerprint: estimate.fingerprint)
            guard state.current(token, session) else { return }
            NotificationCenter.default.post(name: .recipesChanged, object: target.id)
            dismiss()
        } catch {
            guard state.current(token, session) else { return }
            if case APIError.server(409, _) = error {
                self.estimate = nil
                state.error = "Das Rezept wurde inzwischen geändert. Bitte prüfe die Portionsanzahl erneut, bevor du sie übernimmst."
            } else { state.failed(error, token: token, session: session) }
        }
    }
}
