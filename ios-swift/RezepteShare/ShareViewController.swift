import Social
import UniformTypeIdentifiers

final class ShareViewController: SLComposeServiceViewController {
    private var sharedURL: String?

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "Zu Quellenküche"
        placeholder = "Link auf diesem Gerät speichern. Mit deiner Freigabe für den automatischen Linkimport verarbeitet Quellenküche ihn beim nächsten Öffnen über OpenAI. Sonst kannst du im Eingang einmal oder dauerhaft zustimmen. Hier wird nichts hochgeladen."
        loadSharedURL()
    }

    override func isContentValid() -> Bool {
        guard let sharedURL, let url = URL(string: sharedURL),
              ["https", "http"].contains(url.scheme?.lowercased()),
              url.host?.isEmpty == false else {
            return false
        }
        return true
    }

    override func didSelectPost() {
        if let sharedURL, isContentValid() {
            guard SharedImportQueue.enqueue(sharedURL) else {
                let alert = UIAlertController(title: "Link nicht gespeichert",
                    message: "Der Link konnte auf diesem Gerät nicht gespeichert werden. Bitte versuche es erneut oder füge ihn direkt in Quellenküche ein.",
                    preferredStyle: .alert)
                alert.addAction(UIAlertAction(title: "OK", style: .default))
                present(alert, animated: true)
                return
            }
        }
        extensionContext?.completeRequest(returningItems: [], completionHandler: nil)
    }

    override func configurationItems() -> [Any]! { [] }

    private func loadSharedURL() {
        let providers = extensionContext?.inputItems
            .compactMap { $0 as? NSExtensionItem }
            .compactMap(\.attachments)
            .flatMap { $0 } ?? []

        if let provider = providers.first(where: { $0.hasItemConformingToTypeIdentifier(UTType.url.identifier) }) {
            provider.loadItem(forTypeIdentifier: UTType.url.identifier) { [weak self] item, _ in
                let value = (item as? URL)?.absoluteString ?? (item as? String)
                DispatchQueue.main.async {
                    self?.sharedURL = value
                    self?.validateContent()
                }
            }
            return
        }

        if let provider = providers.first(where: { $0.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) }) {
            provider.loadItem(forTypeIdentifier: UTType.plainText.identifier) { [weak self] item, _ in
                let text = item as? String
                let value = text?.split(whereSeparator: { $0.isWhitespace })
                    .map(String.init)
                    .first(where: { $0.hasPrefix("https://") || $0.hasPrefix("http://") })
                DispatchQueue.main.async {
                    self?.sharedURL = value
                    self?.validateContent()
                }
            }
        }
    }
}
