import SwiftUI

@MainActor
struct KitchenTimerView: View {
    let identity: String
    let seconds: Int
    let label: String
    @ObservedObject private var timers = KitchenTimerStore.shared
    @Environment(\.recipeTheme) private var theme
    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            let timer = timers.entries.first { $0.id == identity }
            let remaining = timer?.seconds(at: context.date) ?? seconds
            let running = timer?.endsAt != nil && remaining > 0
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Image(systemName: remaining == 0 ? "bell.fill" : "timer").font(.title2)
                    VStack(alignment: .leading) {
                        Text(remaining == 0 ? "Timer fertig" : String(format: "%d:%02d", remaining / 60, remaining % 60)).font(.title3.bold().monospacedDigit())
                        Text(label).font(.caption).lineLimit(2)
                    }
                    Spacer()
                    Button(timers.startingIDs.contains(identity) ? "Freigabe …" : running ? "Pause" : remaining == 0 ? "Neu" : "Start") {
                        if running { timers.pause(id: identity) }
                        else { Task { await timers.start(id: identity, seconds: seconds, label: label) } }
                    }.buttonStyle(.bordered).frame(minHeight: 44).disabled(timers.startingIDs.contains(identity))
                    if timer != nil {
                        Button { timers.remove(id: identity) } label: { Image(systemName: "xmark.circle") }
                            .frame(width: 44, height: 44).accessibilityLabel("Timer löschen")
                    }
                }
                if let message = timers.message { Text(message).font(.caption).foregroundStyle(theme.warning) }
            }.padding(14).background(theme.surface, in: RoundedRectangle(cornerRadius: 16))
        }
    }
}
