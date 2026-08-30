import SwiftUI

struct RecipeSourceIntegrityView: View {
    let recipeID: Int
    let recipeName: String

    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var report: RecipeSourceIntegrity?
    @State private var isLoading = true
    @State private var isChecking = false
    @State private var isAccepting = false
    @State private var errorMessage: String?

    var body: some View {
        Group {
            if isLoading && report == nil {
                ProgressView("Rezept-TÜV wird geladen …")
            } else if let errorMessage, report == nil {
                ErrorState(message: errorMessage) {
                    Task { await load() }
                }
            } else if let report {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 18) {
                        statusCard(report)
                        qualityCard(report.quality)

                        if let impact = report.impact {
                            impactCard(impact)
                        }

                        if let diff = report.diff, diff.changed {
                            diffCard(diff)
                        }

                        if report.baseline != nil || report.latest != nil {
                            snapshotSection(report)
                        }

                        provenanceCard(report)
                        actionSection(report)
                    }
                    .padding()
                }
                .refreshable { await load() }
                .background(theme.background)
            }
        }
        .navigationTitle("Quellenwächter")
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
    }

    private func impactCard(_ impact: RecipeSourceImpact) -> some View {
        VStack(alignment: .leading, spacing: 13) {
            Label("Änderungswirkung", systemImage: "point.3.connected.trianglepath.dotted")
                .font(.title3.bold())

            if impact.ingredientChanges.isEmpty,
               impact.instructionChanges.isEmpty,
               impact.possibleAllergenChanges.isEmpty {
                Text("Die geänderten Zeilen konnten keiner Rezeptsektion sicher zugeordnet werden.")
                    .font(.subheadline)
                    .foregroundStyle(theme.muted)
            }

            changeGroup("Zutaten", changes: impact.ingredientChanges, icon: "carrot")
            changeGroup("Zubereitung", changes: impact.instructionChanges, icon: "list.number")

            if !impact.possibleAllergenChanges.isEmpty {
                Divider()
                Text("Mögliche Allergen-Auswirkung")
                    .font(.subheadline.bold())
                    .foregroundStyle(theme.warning)
                ForEach(impact.possibleAllergenChanges) { change in
                    HStack(alignment: .top, spacing: 9) {
                        Image(systemName: change.direction == "added" ? "plus.circle.fill" : "minus.circle.fill")
                        VStack(alignment: .leading, spacing: 3) {
                            Text("\(change.label) · \(directionLabel(change.direction))")
                                .font(.subheadline.bold())
                            Text(change.evidence.joined(separator: ", "))
                                .font(.caption)
                                .foregroundStyle(theme.muted)
                        }
                    }
                    .foregroundStyle(theme.warning)
                }
            }

            Label(
                "Nur möglicher Hinweis – keine medizinische Sicherheitsfreigabe. Zutaten und Produktetiketten vollständig prüfen.",
                systemImage: "exclamationmark.shield"
            )
            .font(.caption.bold())
            .foregroundStyle(theme.warning)
        }
        .cardSurface()
    }

    @ViewBuilder
    private func changeGroup(
        _ title: String,
        changes: [SourceContentChange],
        icon: String
    ) -> some View {
        if !changes.isEmpty {
            VStack(alignment: .leading, spacing: 7) {
                Label(title, systemImage: icon)
                    .font(.subheadline.bold())
                ForEach(changes) { change in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Image(systemName: change.direction == "added" ? "plus" : "minus")
                            .foregroundStyle(change.direction == "added" ? theme.success : theme.danger)
                        Text(change.text)
                            .font(.caption)
                    }
                }
            }
        }
    }

    private func directionLabel(_ direction: String) -> String {
        direction == "added" ? "hinzugekommen" : "entfernt"
    }

    private func statusCard(_ report: RecipeSourceIntegrity) -> some View {
        HStack(alignment: .top, spacing: 16) {
            Image(systemName: statusIcon(report.status))
                .font(.system(size: 28, weight: .semibold))
                .foregroundStyle(statusColor(report.status))
                .frame(width: 52, height: 52)
                .background(statusColor(report.status).opacity(0.12), in: Circle())

            VStack(alignment: .leading, spacing: 7) {
                Text(statusTitle(report.status))
                    .font(.title3.bold())
                Text(statusDetail(report.status))
                    .font(.subheadline)
                    .foregroundStyle(theme.muted)
                if let checkedAt = report.checkedAt {
                    Label(
                        Date(timeIntervalSince1970: checkedAt).formatted(
                            date: .abbreviated,
                            time: .shortened
                        ),
                        systemImage: "clock"
                    )
                    .font(.caption)
                    .foregroundStyle(theme.muted)
                }
            }
            Spacer(minLength: 0)
        }
        .cardSurface()
    }

    private func qualityCard(_ quality: RecipeQualityReport) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 14) {
                ZStack {
                    Circle()
                        .stroke(theme.outline, lineWidth: 7)
                    Circle()
                        .trim(from: 0, to: CGFloat(quality.score) / 100)
                        .stroke(
                            qualityColor(quality.score),
                            style: StrokeStyle(lineWidth: 7, lineCap: .round)
                        )
                        .rotationEffect(.degrees(-90))
                    Text("\(quality.score)")
                        .font(.headline.monospacedDigit())
                }
                .frame(width: 64, height: 64)

                VStack(alignment: .leading, spacing: 4) {
                    Text("Rezept-TÜV")
                        .font(.title3.bold())
                    Text("\(quality.checkedRules) nachvollziehbare Regeln · \(quality.issues.count) Hinweis(e)")
                        .font(.caption)
                        .foregroundStyle(theme.muted)
                }
            }

            if quality.issues.isEmpty {
                Label("Keine offenen Qualitäts-Hinweise", systemImage: "checkmark.seal.fill")
                    .foregroundStyle(theme.success)
            } else {
                Divider()
                ForEach(quality.issues) { issue in
                    HStack(alignment: .top, spacing: 11) {
                        Image(systemName: issueIcon(issue.severity))
                            .foregroundStyle(issueColor(issue.severity))
                            .frame(width: 22)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(issue.title).font(.subheadline.bold())
                            Text(issue.detail)
                                .font(.caption)
                                .foregroundStyle(theme.muted)
                        }
                        Spacer(minLength: 0)
                    }
                }
            }
        }
        .cardSurface()
    }

    private func diffCard(_ diff: RecipeSourceDiff) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("Quelle hat sich geändert", systemImage: "arrow.triangle.2.circlepath")
                    .font(.title3.bold())
                    .foregroundStyle(theme.warning)
                Spacer()
                Text("\(Int(diff.similarity * 100)) % ähnlich")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(theme.muted)
            }

            Text("+\(diff.addedLines) / −\(diff.removedLines) Zeilen. Das gespeicherte Rezept wurde nicht verändert.")
                .font(.subheadline)
                .foregroundStyle(theme.muted)

            ScrollView(.horizontal) {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(Array(diff.lines.enumerated()), id: \.offset) { _, line in
                        Text(line.isEmpty ? " " : line)
                            .font(.caption.monospaced())
                            .foregroundStyle(diffLineColor(line))
                            .textSelection(.enabled)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(12)
            .background(theme.background, in: RoundedRectangle(cornerRadius: 12))

            if diff.truncated {
                Text("Die Vorschau wurde gekürzt.")
                    .font(.caption)
                    .foregroundStyle(theme.muted)
            }
        }
        .cardSurface()
    }

    private func snapshotSection(_ report: RecipeSourceIntegrity) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Quellfassungen")
                .font(.title3.bold())

            if let baseline = report.baseline {
                snapshotCard("Gespeicherter Vergleichsstand", snapshot: baseline)
            }
            if let latest = report.latest, latest.id != report.baseline?.id {
                snapshotCard("Zuletzt beobachtet", snapshot: latest)
            }
        }
    }

    private func snapshotCard(_ title: String, snapshot: RecipeSourceSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Text(title).font(.subheadline.bold())
                Spacer()
                if snapshot.isBaseline {
                    Label("Baseline", systemImage: "bookmark.fill")
                        .font(.caption.bold())
                        .foregroundStyle(theme.accentPressed)
                }
            }
            if let pageTitle = snapshot.pageTitle, !pageTitle.isEmpty {
                Text(pageTitle).font(.subheadline)
            }
            if let preview = snapshot.preview, !preview.isEmpty {
                Text(preview)
                    .font(.caption.monospaced())
                    .foregroundStyle(theme.muted)
                    .lineLimit(10)
                    .textSelection(.enabled)
            }
            if let error = snapshot.error, !error.isEmpty {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(theme.warning)
            }
            Text(Date(timeIntervalSince1970: snapshot.checkedAt).formatted(date: .abbreviated, time: .shortened))
                .font(.caption2)
                .foregroundStyle(theme.muted)
        }
        .cardSurface()
    }

    private func provenanceCard(_ report: RecipeSourceIntegrity) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Herkunft & Freigabe")
                .font(.title3.bold())
            LabeledContent("Quelle", value: report.platform)
            LabeledContent(
                "Zutaten",
                value: report.verified ? "Manuell geprüft" : "Noch nicht freigegeben"
            )
            if let verifiedAt = report.verifiedAt {
                LabeledContent(
                    "Geprüft",
                    value: Date(timeIntervalSince1970: verifiedAt).formatted(
                        date: .abbreviated,
                        time: .shortened
                    )
                )
            }
            if let verifiedBy = report.verifiedBy, !verifiedBy.isEmpty {
                LabeledContent("Geprüft von", value: verifiedBy)
            }
            Label(
                "Quellprüfungen überschreiben niemals Rezeptdaten.",
                systemImage: "lock.shield"
            )
            .font(.caption)
            .foregroundStyle(theme.success)

            if let rawURL = report.sourceUrl, let url = URL(string: rawURL), url.scheme == "https" {
                Link(destination: url) {
                    Label("Originalquelle öffnen", systemImage: "arrow.up.right.square")
                }
            }
        }
        .cardSurface()
    }

    @ViewBuilder
    private func actionSection(_ report: RecipeSourceIntegrity) -> some View {
        if session.fullAccess,
           report.sourceUrl?.hasPrefix("https://") == true,
           ["unchecked", "current", "changed", "unavailable"].contains(report.status) {
            VStack(spacing: 10) {
                Button {
                    Task { await checkSource() }
                } label: {
                    Label(
                        isChecking ? "Quelle wird geprüft …" : "Quelle jetzt prüfen",
                        systemImage: "checkmark.shield"
                    )
                    .frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(.borderedProminent)
                .tint(theme.accentPressed)
                .disabled(isChecking || isAccepting)

                if report.status == "changed" {
                    Button {
                        Task { await acceptLatest() }
                    } label: {
                        Label(
                            isAccepting ? "Wird bestätigt …" : "Als neuen Quellstand bestätigen",
                            systemImage: "bookmark"
                        )
                        .frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .buttonStyle(.bordered)
                    .disabled(isChecking || isAccepting)
                }
            }
        }
    }

    private func load() async {
        isLoading = report == nil
        defer { isLoading = false }
        do {
            report = try await session.api.recipeSourceIntegrity(id: recipeID)
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func checkSource() async {
        guard !isChecking else { return }
        isChecking = true
        defer { isChecking = false }
        do {
            report = try await session.api.checkRecipeSourceIntegrity(id: recipeID)
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func acceptLatest() async {
        guard !isAccepting else { return }
        isAccepting = true
        defer { isAccepting = false }
        do {
            report = try await session.api.acceptRecipeSourceIntegrity(id: recipeID)
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
            session.handle(error)
        }
    }

    private func statusTitle(_ status: String) -> String {
        switch status {
        case "current": "Quelle unverändert"
        case "changed": "Änderung erkannt"
        case "unavailable": "Quelle nicht erreichbar"
        case "local": "Lokale Originalquelle"
        case "missing": "Originalquelle fehlt"
        default: "Quelle noch ungeprüft"
        }
    }

    private func statusDetail(_ status: String) -> String {
        switch status {
        case "current": "Der beobachtete Text stimmt mit dem gespeicherten Vergleichsstand überein."
        case "changed": "Die neue Fassung wartet auf eine manuelle Prüfung."
        case "unavailable": "Das gespeicherte Rezept bleibt vollständig erhalten."
        case "local": "PDF, Foto oder eigener Eintrag werden lokal nachvollzogen."
        case "missing": "Eine Herkunft kann in den Rezeptinformationen ergänzt werden."
        default: "Starte einen sicheren Vergleich mit der Originalseite."
        }
    }

    private func statusIcon(_ status: String) -> String {
        switch status {
        case "current": "checkmark.shield.fill"
        case "changed": "exclamationmark.arrow.triangle.2.circlepath"
        case "unavailable": "wifi.slash"
        case "local": "doc.badge.checkmark"
        case "missing": "link.badge.plus"
        default: "shield.lefthalf.filled"
        }
    }

    private func statusColor(_ status: String) -> Color {
        switch status {
        case "current", "local": theme.success
        case "changed", "unavailable", "missing": theme.warning
        default: theme.accentPressed
        }
    }

    private func qualityColor(_ score: Int) -> Color {
        if score >= 85 { return theme.success }
        if score >= 60 { return theme.warning }
        return theme.danger
    }

    private func issueIcon(_ severity: String) -> String {
        switch severity {
        case "critical": "xmark.octagon.fill"
        case "warning": "exclamationmark.triangle.fill"
        default: "info.circle.fill"
        }
    }

    private func issueColor(_ severity: String) -> Color {
        switch severity {
        case "critical": theme.danger
        case "warning": theme.warning
        default: theme.accentPressed
        }
    }

    private func diffLineColor(_ line: String) -> Color {
        if line.hasPrefix("+") && !line.hasPrefix("+++") { return theme.success }
        if line.hasPrefix("-") && !line.hasPrefix("---") { return theme.danger }
        return theme.muted
    }
}
