import SwiftUI

/// A single in-flight operation per sheet. Tokens also reject late responses after a session switch.
@MainActor
final class HouseholdFeatureState: ObservableObject {
    @Published var busy = false
    @Published var error: String?
    @Published var notice: String?
    @Published private(set) var identity: UUID?
    private var operation = UUID()

    func reset(_ session: SessionStore) {
        operation = UUID()
        identity = session.identity
        busy = false
        error = nil
        notice = nil
    }

    func begin(_ session: SessionStore) -> UUID? {
        guard !busy, !session.readOnly, session.role != .guest,
              identity == session.identity else { return nil }
        busy = true
        error = nil
        notice = nil
        operation = UUID()
        return operation
    }

    func current(_ token: UUID, _ session: SessionStore) -> Bool {
        operation == token && identity == session.identity && !Task.isCancelled
    }

    func finish(_ token: UUID, _ session: SessionStore) {
        // Cancellation suppresses payloads, but must not leave a mounted sheet locked.
        if operation == token, identity == session.identity { busy = false }
    }

    func failed(_ error: Error, token: UUID, session: SessionStore) {
        guard current(token, session) else { return }
        self.error = error.localizedDescription
        session.handle(error)
    }
}

@MainActor
struct HouseholdFeedback: View {
    @ObservedObject var state: HouseholdFeatureState
    var body: some View {
        if state.busy { ProgressView().accessibilityLabel("Wird geladen") }
        if let error = state.error { Text(error).foregroundStyle(.red).accessibilityAddTraits(.updatesFrequently) }
        if let notice = state.notice { Text(notice).foregroundStyle(.secondary) }
    }
}
