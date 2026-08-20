import SwiftUI

struct ManualCareBanner: View {
    let reasons: [String]

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(AppTheme.warning)
            VStack(alignment: .leading, spacing: 4) {
                Text("Manuell pflegen")
                    .font(.headline)
                Text(reasons.isEmpty
                     ? "Zutaten oder Zubereitungsschritte fehlen."
                     : reasons.joined(separator: " · "))
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .cardSurface()
        .accessibilityElement(children: .combine)
    }
}

struct EmptyState: View {
    let icon: String
    let title: String
    let message: String

    var body: some View {
        ContentUnavailableView(title, systemImage: icon, description: Text(message))
    }
}

struct ErrorState: View {
    let message: String
    let retry: () -> Void

    var body: some View {
        ContentUnavailableView {
            Label("Das hat nicht geklappt", systemImage: "wifi.exclamationmark")
        } description: {
            Text(message)
        } actions: {
            Button("Erneut versuchen", action: retry)
                .buttonStyle(.borderedProminent)
        }
    }
}

