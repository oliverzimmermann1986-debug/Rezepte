import Combine
import Foundation

enum AIProcessingAction: String, Sendable {
    case importLink, importFile, scanPhoto, reanalyze, extractSource, translation, saveRecipe
    case nutrition, generateImage, imageBackfill, libraryAudit, shoppingOptimization, connectionTest, retryImport

    var title: String {
        switch self {
        case .importLink, .importFile, .retryImport, .saveRecipe: "Rezept mit KI erkennen"
        case .scanPhoto: "Rezeptfoto mit KI ergänzen"
        case .reanalyze, .extractSource: "Rezept erneut mit KI prüfen"
        case .translation: "Quelltext mit KI übersetzen"
        case .nutrition: "Nährwerte mit KI schätzen"
        case .generateImage: "Rezeptbild mit KI erstellen"
        case .imageBackfill: "Rezeptbilder der Bibliothek erstellen"
        case .libraryAudit: "Bibliothek mit KI prüfen"
        case .shoppingOptimization: "Einkaufsliste mit KI sortieren"
        case .connectionTest: "OpenAI-Verbindung testen"
        }
    }

    var dataDescription: String {
        switch self {
        case .importLink, .retryImport, .extractSource, .reanalyze, .saveRecipe:
            "Quelllink und Rezeptinhalte: Texte, Bilder und gegebenenfalls PDF-Inhalte, Einzelbilder aus Videos sowie aus Audio gewonnene Inhalte."
        case .importFile:
            "Die ausgewählte Foto- oder PDF-Datei mit ihrem Text- und Bildinhalt."
        case .scanPhoto:
            "Das ausgewählte Foto und die zugehörigen Rezept- und Quellinformationen."
        case .translation:
            "Der angezeigte Rezept- oder Quelltext und die gewählte Zielsprache."
        case .nutrition:
            "Zutaten, Mengen und Portionsangaben dieses Rezepts."
        case .generateImage:
            "Rezeptname, Zutaten, Beschreibung und gegebenenfalls das vorhandene Rezeptbild."
        case .imageBackfill:
            "Rezeptnamen, Zutaten, Beschreibungen und gegebenenfalls Bilder der Rezepte im gestarteten Bibliothekslauf."
        case .libraryAudit:
            "Namen, Kategorien und Beschreibungstexte der globalen Rezepte im gestarteten Bibliothekslauf."
        case .shoppingOptimization:
            "Artikel, Mengen, Einheiten und Kategorien deiner aktuellen Einkaufsliste."
        case .connectionTest:
            "Eine technische Testanfrage und das gewählte Modell. Der API-Schlüssel wird zur Authentifizierung verwendet; keine Rezeptdaten werden für diesen Test ausgewählt."
        }
    }

    var purpose: String {
        switch self {
        case .importLink, .importFile, .retryImport, .scanPhoto, .reanalyze, .extractSource, .saveRecipe:
            "Rezept, Zutaten und Zubereitung erkennen oder ergänzen; je nach Import die Beschreibung übersetzen und ein passendes Rezeptbild erstellen."
        case .translation: "Den Quelltext in die gewählte Sprache übersetzen."
        case .nutrition: "Nährwerte aus den Zutaten schätzen."
        case .generateImage, .imageBackfill: "Passende Rezeptbilder generieren."
        case .libraryAudit: "Unstimmigkeiten bei Namen, Kategorien und Beschreibungen finden."
        case .shoppingOptimization: "Artikelnamen vereinheitlichen und Kategorien vorschlagen."
        case .connectionTest: "Prüfen, ob die konfigurierte OpenAI-Verbindung funktioniert."
        }
    }
}

struct AIProcessingConsent: Sendable {
    static let version = "openai-recipe-v1"
    let id: UUID
    let action: AIProcessingAction
    let identity: UUID
    let server: String
}

struct AIConsentRequest: Identifiable, Sendable {
    let id = UUID()
    let action: AIProcessingAction
    let identity: UUID
    let server: String
    var host: String { URL(string: server)?.host ?? "ausgewählter Server" }
}

/// One decision for one action. Nothing is written to defaults or the keychain.
@MainActor
final class AIConsentCoordinator: ObservableObject {
    @Published private(set) var pending: AIConsentRequest?
    private var continuation: CheckedContinuation<AIProcessingConsent?, Never>?

    func request(_ action: AIProcessingAction, session: SessionStore) async -> AIProcessingConsent? {
        let identity = session.identity
        let server = APIClient.normalizedServerURL(session.savedServer)?.absoluteString ?? ""
        guard case .signedIn = session.state, !server.isEmpty else { return nil }
        let consent = await request(action, identity: identity, server: server)
        guard session.identity == identity,
              APIClient.normalizedServerURL(session.savedServer)?.absoluteString == server,
              case .signedIn = session.state else { return nil }
        return consent
    }

    func request(_ action: AIProcessingAction, identity: UUID, server: String) async -> AIProcessingConsent? {
        guard pending == nil, !Task.isCancelled else { return nil }
        let request = AIConsentRequest(action: action, identity: identity, server: server)
        return await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                self.continuation = continuation
                pending = request
                if Task.isCancelled { cancel() }
            }
        } onCancel: {
            Task { @MainActor [weak self] in
                if self?.pending?.id == request.id { self?.cancel() }
            }
        }
    }

    func approve(identity: UUID, server: String) {
        guard let request = pending, request.identity == identity,
              request.server == APIClient.normalizedServerURL(server)?.absoluteString else { cancel(); return }
        finish(AIProcessingConsent(id: request.id, action: request.action, identity: request.identity, server: request.server))
    }

    func cancel() { finish(nil) }

    private func finish(_ consent: AIProcessingConsent?) {
        let suspended = continuation
        continuation = nil
        pending = nil
        suspended?.resume(returning: consent)
    }
}
