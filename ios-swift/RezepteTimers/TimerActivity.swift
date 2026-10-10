import ActivityKit
import SwiftUI
import WidgetKit

@main struct KitchenTimerWidget: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: KitchenTimerAttributes.self) { context in
            HStack {
                Image(systemName: "timer").font(.title)
                VStack(alignment: .leading) {
                    Text(context.state.label).font(.headline).lineLimit(2)
                    if context.isStale { Text("Timer fertig").font(.title2.bold()) }
                    else { Text(timerInterval: Date()...max(Date(), context.state.endsAt), countsDown: true).font(.title2.bold().monospacedDigit()) }
                }
            }.padding().activityBackgroundTint(Color(red: 0.96, green: 0.88, blue: 0.62))
                .activitySystemActionForegroundColor(.black)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) { Image(systemName: "timer") }
                DynamicIslandExpandedRegion(.trailing) {
                    Text(timerInterval: Date()...max(Date(), context.state.endsAt), countsDown: true).monospacedDigit()
                }
                DynamicIslandExpandedRegion(.bottom) { Text(context.state.label).lineLimit(2) }
            } compactLeading: { Image(systemName: "timer") }
              compactTrailing: { Text(timerInterval: Date()...max(Date(), context.state.endsAt), countsDown: true).monospacedDigit().frame(width: 48) }
              minimal: { Image(systemName: "timer") }
        }
    }
}
