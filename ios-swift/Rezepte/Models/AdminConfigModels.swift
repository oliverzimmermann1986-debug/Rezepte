import Foundation

struct NativeAdminConfig: Codable {
    let ai: NativeAdminAIConfig?
    let mail: NativeAdminMailConfig?
    let pdf: NativeAdminPDFConfig?
    let einkauf: NativeAdminEinkaufConfig?
}

struct NativeAdminAIConfig: Codable {
    let openai: NativeAdminOpenAIConfig?
    let confidenceThreshold: Double?
    let autoTranslate: Bool?
    let videoFallback: NativeAdminVideoFallbackConfig?
    let imageGeneration: NativeAdminImageGenerationConfig?
}

struct NativeAdminOpenAIConfig: Codable {
    let apiKey: String?
    let model: String?
    let baseUrl: String?
    let timeout: Int?
}

struct NativeAdminVideoFallbackConfig: Codable {
    let enabled: Bool?
    let maxFrames: Int?
    let maxSeconds: Int?
    let transcriptionModel: String?
}

struct NativeAdminImageGenerationConfig: Codable {
    let enabled: Bool?
    let model: String?
    let size: String?
    let quality: String?
    let outputFormat: String?
}

struct NativeAdminMailConfig: Codable {
    let recipe: NativeAdminMailAccountConfig?
    let wedding: NativeAdminMailAccountConfig?
}

struct NativeAdminMailAccountConfig: Codable {
    let enabled: Bool?
    let imapHost: String?
    let imapPort: Int?
    let username: String?
    let password: String?
    let folder: String?
    let maxMails: Int?
    let attachmentMaxMb: Int?
    let defaultCategory: String?
    let alwaysPending: Bool?
}

struct NativeAdminPDFConfig: Codable {
    let autoRotate: Bool?
    let useTesseractOsd: Bool?
    let useOcrVote: Bool?
    let removeBlankPages: Bool?
    let autoCrop: Bool?
    let deskewScans: Bool?
    let ocrScans: Bool?
    let ocrLanguage: String?
    let improveContrast: Bool?
    let sharpenScans: Bool?
    let scanDpi: Int?
    let keepOriginal: Bool?
}

struct NativeAdminEinkaufConfig: Codable {
    let apiUrl: String?
    let appToken: String?
    let cfAccessClientId: String?
    let cfAccessClientSecret: String?
    let autoConsolidate: Bool?
}

struct NativeAdminConfigPatch: Encodable {
    let ai: NativeAdminAIConfigPatch
    let mail: NativeAdminMailConfigPatch
    let pdf: NativeAdminPDFConfigPatch
    let einkauf: NativeAdminEinkaufConfigPatch
}

struct NativeAdminAIConfigPatch: Encodable {
    let openai: NativeAdminOpenAIConfigPatch
    let confidenceThreshold: Double
    let autoTranslate: Bool
    let videoFallback: NativeAdminVideoFallbackConfigPatch
    let imageGeneration: NativeAdminImageGenerationConfigPatch
}

struct NativeAdminOpenAIConfigPatch: Encodable {
    let apiKey: String?
    let model: String
    let timeout: Int
}

struct NativeAdminVideoFallbackConfigPatch: Encodable {
    let enabled: Bool
    let maxFrames: Int
    let maxSeconds: Int
    let transcriptionModel: String
}

struct NativeAdminImageGenerationConfigPatch: Encodable {
    let enabled: Bool
    let model: String
    let size: String
    let quality: String
    let outputFormat: String
}

struct NativeAdminMailConfigPatch: Encodable {
    let recipe: NativeAdminMailAccountConfigPatch
    let wedding: NativeAdminMailAccountConfigPatch
}

struct NativeAdminMailAccountConfigPatch: Encodable {
    let enabled: Bool
    let imapHost: String
    let imapPort: Int
    let username: String
    let password: String?
    let folder: String
    let maxMails: Int
    let attachmentMaxMb: Int
    let defaultCategory: String?
    let alwaysPending: Bool?
}

struct NativeAdminPDFConfigPatch: Encodable {
    let autoRotate: Bool
    let useTesseractOsd: Bool
    let useOcrVote: Bool
    let removeBlankPages: Bool
    let autoCrop: Bool
    let deskewScans: Bool
    let ocrScans: Bool
    let ocrLanguage: String
    let improveContrast: Bool
    let sharpenScans: Bool
    let scanDpi: Int
    let keepOriginal: Bool
}

struct NativeAdminEinkaufConfigPatch: Encodable {
    let appToken: String?
    let cfAccessClientId: String
    let cfAccessClientSecret: String?
    let autoConsolidate: Bool
}

struct NativeAdminScheduleStatus: Codable {
    let scraper: NativeAdminScheduleItem?
}

struct NativeAdminScheduleItem: Codable {
    let oncalendar: String?
    let unit: String?
    let nextRun: String?
    let lastRun: Double?
}

struct NativeAdminSchedulePreview: Codable {
    let scraper: NativeAdminSchedulePreviewItem?
}

struct NativeAdminSchedulePreviewItem: Codable {
    let ok: Bool
    let error: String?
    let nextRuns: [String]?
}

struct NativeAdminTestResult: Codable {
    let ok: Bool
    let message: String?
    let error: String?

    var displayMessage: String {
        message?.nilIfEmpty ?? error?.nilIfEmpty ?? (ok ? "Verbindung erfolgreich." : "Verbindung fehlgeschlagen.")
    }
}

struct NativeAdminLogStats: Codable {
    let path: String
    let exists: Bool
    let count: Int
    let totalBytes: Int
    let oldestAgeDays: Double?
    let retentionDays: Int?
}

struct NativeAdminBackupList: Codable {
    let tiers: [String: [NativeAdminBackupItem]]
    let backupsRoot: String

    var allBackups: [NativeAdminBackupItem] {
        tiers.values.flatMap { $0 }.sorted { $0.mtime > $1.mtime }
    }
}

struct NativeAdminBackupItem: Codable, Identifiable {
    let name: String
    let path: String
    let sizeBytes: Int64
    let mtime: Double

    var id: String { path }
}

struct NativeAdminOperationResult: Codable {
    let ok: Bool
    let stdout: String?
    let stderr: String?
    let error: String?

    var displayMessage: String {
        if ok {
            return stdout?.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
                ?? "Aktion erfolgreich abgeschlossen."
        }
        return error?.nilIfEmpty
            ?? stderr?.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
            ?? "Aktion fehlgeschlagen."
    }
}
