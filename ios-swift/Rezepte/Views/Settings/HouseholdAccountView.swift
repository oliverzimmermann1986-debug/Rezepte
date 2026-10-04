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
                    Text("Private Rezepte, Favoriten, Bewertungen, Einkäufe und Wochenpläne gehören diesem Haushalt. Eine zweite Person nutzt ihn mit eigener Anmeldung.")
                        .font(.caption)
                        .foregroundStyle(theme.muted)
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
        .task { await load() }
        .refreshable { await load() }
    }

    private func load() async {
        guard !session.readOnly else { return }
        let requestID = UUID()
        loadID = requestID
        isLoading = true
        defer { if loadID == requestID { isLoading = false } }
        do {
            let result = try await session.api.account()
            guard loadID == requestID else { return }
            account = result
        } catch {
            guard loadID == requestID else { return }
            message = error.localizedDescription
            session.handle(error)
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
