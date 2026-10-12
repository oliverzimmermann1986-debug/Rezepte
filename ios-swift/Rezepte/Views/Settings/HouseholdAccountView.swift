import SwiftUI
import UIKit

struct HouseholdAccountView: View {
    @EnvironmentObject private var session: SessionStore
    @Environment(\.recipeTheme) private var theme
    @State private var account: HouseholdAccount?
    @State private var invitationInput = ""
    @State private var inviteURL: URL?
    @State private var isLoading = false
    @State private var isWorking = false
    @State private var message: String?
    @State private var loadID = UUID()
    @State private var defaultServings = 2

    var body: some View {
        List {
            if session.readOnly {
                Section("Als Gast ansehen") {
                    Text("Globale Rezepte sind für alle sichtbar. Mit einem Konto erhältst du eine eigene Sammlung, Einkaufsliste und Wochenplanung.")
                    Button("Konto erstellen") { session.startRegistration() }
                    Button("Anmelden") { session.signOut() }
                }
            } else if let account {
                Section("Mein Haushalt") {
                    ForEach(account.members) { member in
                        LabeledContent(member.username, value: member.disabled == true ? "Deaktiviert" : "Aktiv")
                    }
                    Text("Rezepte sind global gespeichert. Der Haushalts-Haken, Favoriten, Bewertungen, Einkäufe und Wochenpläne gehören diesem Haushalt. Eine zweite Person nutzt ihn mit eigener Anmeldung.")
                        .font(.caption)
                        .foregroundStyle(theme.muted)
                }
                if session.supports("household-default-servings-v1") {
                    Section("Standard-Portionen") {
                        Stepper("\(defaultServings) \(defaultServings == 1 ? "Portion" : "Portionen")", value: $defaultServings, in: 1...24)
                            .disabled(isWorking)
                        Text("Gilt für alle Mitglieder bei neuen Wochenvorschlägen, Planungen und Einkäufen. Du kannst die Portionen pro Gericht ändern. Bestehende Planungen bleiben erhalten.")
                            .font(.caption).foregroundStyle(theme.muted)
                        Button("Portionszahl speichern") { Task { await saveDefaultServings() } }
                            .disabled(isWorking || defaultServings == HouseholdServingDefaults.resolve(account.defaultServings))
                    }
                }
                if account.isOwner {
                    Section("Zweite Person einladen") {
                        if account.members.count < account.maxMembers {
                            Button("Einladungslink erstellen") { Task { await createInvitation() } }
                                .disabled(isWorking)
                            Text("Der Link gilt sieben Tage und kann einmal verwendet werden. Ein neuer Link ersetzt die vorherige offene Einladung.")
                                .font(.caption)
                                .foregroundStyle(theme.muted)
                        } else {
                            Label("Der Haushalt hat bereits zwei Personen", systemImage: "person.2.fill")
                        }
                        if let inviteURL {
                            ShareLink(item: inviteURL) { Label("Einladung teilen", systemImage: "square.and.arrow.up") }
                            Button("Link kopieren") {
                                UIPasteboard.general.string = inviteURL.absoluteString
                                message = "Einladungslink kopiert."
                            }
                        }
                        ForEach(account.invitations.filter(\.isActive)) { invitation in
                            HStack {
                                Text("Gültig bis \(Date(timeIntervalSince1970: invitation.expiresAt).formatted(date: .abbreviated, time: .omitted))")
                                    .font(.caption)
                                Spacer()
                                Button("Widerrufen", role: .destructive) { Task { await revoke(invitation) } }
                                    .disabled(isWorking)
                            }
                        }
                    }
                }
                Section("Einladung annehmen") {
                    TextField("Einladungslink oder Code", text: $invitationInput)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Text("Deine bisherigen privaten Rezepte und Listen werden beim Beitritt in den gemeinsamen Haushalt übernommen.")
                        .font(.caption)
                        .foregroundStyle(theme.muted)
                    Button("Gemeinsamem Haushalt beitreten") { Task { await acceptInvitation() } }
                        .disabled(isWorking || HouseholdInvitationInput.token(from: invitationInput).count < 16)
                }
            }
            if isLoading || isWorking { ProgressView() }
            if let message {
                Section { Text(message).foregroundStyle(theme.muted) }
            }
        }
        .scrollContentBackground(.hidden)
        .background(theme.background)
        .navigationTitle("Mein Haushalt")
        .task(id: session.identity) {
            account = nil; inviteURL = nil; message = nil; defaultServings = 2
            await load()
        }
        .refreshable { await load() }
    }

    private func load() async {
        guard !session.readOnly else { return }
        let identity = session.identity
        let requestID = UUID()
        loadID = requestID
        isLoading = true
        defer { if loadID == requestID { isLoading = false } }
        do {
            let result = try await session.api.account()
            guard loadID == requestID, session.identity == identity else { return }
            account = result
            defaultServings = HouseholdServingDefaults.resolve(result.defaultServings)
            session.adoptHousehold(result, expectedIdentity: identity)
        } catch {
            guard loadID == requestID, session.identity == identity else { return }
            message = error.localizedDescription
            session.handle(error)
        }
    }

    private func saveDefaultServings() async {
        guard !isWorking else { return }
        let identity = session.identity, value = defaultServings
        isWorking = true; defer { isWorking = false }
        do {
            try await session.saveHouseholdDefaultServings(value)
            guard session.identity == identity else { return }
            await load()
            message = "Standard-Portionszahl für den Haushalt gespeichert."
        } catch {
            guard session.identity == identity else { return }
            message = error.localizedDescription; session.handle(error)
        }
    }

    private func createInvitation() async {
        guard !isWorking else { return }
        isWorking = true
        defer { isWorking = false }
        do {
            let invitation = try await session.api.createInvitation()
            inviteURL = try await session.api.invitationURL(path: invitation.invitePath)
            message = "Die zweite Person kann den Link zur Registrierung oder in ihrem bestehenden Konto verwenden."
            await load()
        } catch {
            message = error.localizedDescription
            session.handle(error)
        }
    }

    private func revoke(_ invitation: HouseholdInvitation) async {
        guard !isWorking else { return }
        isWorking = true
        defer { isWorking = false }
        do {
            _ = try await session.api.revokeInvitation(id: invitation.id)
            inviteURL = nil
            message = "Einladung widerrufen."
            await load()
        } catch {
            message = error.localizedDescription
            session.handle(error)
        }
    }

    private func acceptInvitation() async {
        guard !isWorking else { return }
        isWorking = true
        defer { isWorking = false }
        do {
            _ = try await session.api.acceptInvitation(invitationInput)
            try await session.householdDidChange()
            session.alertMessage = "Du nutzt jetzt den gemeinsamen Haushalt."
        } catch {
            message = error.localizedDescription
            session.handle(error)
        }
    }
}
