import Social
import UniformTypeIdentifiers

final class ShareViewController: SLComposeServiceViewController {
    private var sharedURL: String?

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "Zu Quellenküche"
        placeholder = "Rezeptlink aus Website, Pinterest oder YouTube importieren"
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
            SharedImportQueue.enqueue(sharedURL)
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
