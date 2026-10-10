import AVFoundation
import Combine
import Foundation
import Speech

enum CookingVoiceCommand: Equatable {
    case next, previous, done, read, startTimer, pauseTimer, stop
    static func parse(_ text: String) -> CookingVoiceCommand? {
        let value = text.lowercased().components(separatedBy: CharacterSet.letters.inverted)
            .filter { !$0.isEmpty }.joined(separator: " ")
        switch value {
        case "weiter", "nächster schritt": return .next
        case "zurück", "vorheriger schritt": return .previous
        case "erledigt", "schritt erledigt": return .done
        case "vorlesen", "schritt vorlesen": return .read
        case "timer starten", "timer start": return .startTimer
        case "timer pausieren", "timer pause": return .pauseTimer
        case "sprachsteuerung aus", "sprachsteuerung ausschalten": return .stop
        default: return nil
        }
    }
}

@MainActor final class CookingVoiceController: NSObject, ObservableObject, AVSpeechSynthesizerDelegate {
    @Published private(set) var listening = false
    @Published private(set) var reading = false
    @Published var message: String?
    var onCommand: ((CookingVoiceCommand) -> Void)?
    private let engine = AVAudioEngine()
    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "de-DE"))
    private let speaker = AVSpeechSynthesizer()
    private var task: SFSpeechRecognitionTask?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var tapInstalled = false
    private var wanted = false
    private var cycle = UUID()
    private var permissionFlight = false
    private var currentUtterance: AVSpeechUtterance?

    override init() { super.init(); speaker.delegate = self }

    func start() async {
        guard !permissionFlight, !wanted, !listening, !reading else { return }
        guard recognizer?.supportsOnDeviceRecognition == true else {
            message = "Die deutsche Spracherkennung ist auf diesem iPhone nicht lokal verfügbar."; return
        }
        permissionFlight = true
        let expected = cycle
        defer { permissionFlight = false }
        let speech = await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
            SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0 == .authorized) }
        }
        guard cycle == expected, !Task.isCancelled else { return }
        let microphone = await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
            AVAudioSession.sharedInstance().requestRecordPermission { continuation.resume(returning: $0) }
        }
        guard cycle == expected, !Task.isCancelled else { return }
        guard speech && microphone else { message = "Bitte Mikrofon und Spracherkennung in den iPhone-Einstellungen freigeben."; return }
        wanted = true; message = nil
        beginRecognition()
    }
    func stop() {
        wanted = false; cycle = UUID(); listening = false; reading = false
        currentUtterance = nil
        stopRecognition(); speaker.stopSpeaking(at: .immediate)
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
    private func stopRecognition() {
        engine.stop()
        if tapInstalled { engine.inputNode.removeTap(onBus: 0); tapInstalled = false }
        request?.endAudio(); task?.cancel(); task = nil; request = nil; listening = false
    }
    private func beginRecognition() {
        guard wanted, !reading else { return }
        guard recognizer?.supportsOnDeviceRecognition == true, recognizer?.isAvailable == true else {
            stop(); message = "Die lokale Spracherkennung ist gerade nicht verfügbar. Du kannst sie später erneut einschalten."; return
        }
        stopRecognition()
        cycle = UUID(); let expected = cycle
        do {
            let audio = AVAudioSession.sharedInstance()
            try audio.setCategory(.playAndRecord, mode: .measurement, options: [.defaultToSpeaker, .allowBluetooth])
            try audio.setActive(true)
            let buffer = SFSpeechAudioBufferRecognitionRequest()
            buffer.requiresOnDeviceRecognition = true
            buffer.shouldReportPartialResults = false
            request = buffer
            let node = engine.inputNode, format = engine.inputNode.outputFormat(forBus: 0)
            guard format.sampleRate > 0 && format.channelCount > 0 else { throw APIError.invalidResponse("Mikrofon") }
            node.installTap(onBus: 0, bufferSize: 1024, format: format) { audioBuffer, _ in buffer.append(audioBuffer) }
            tapInstalled = true; engine.prepare(); try engine.start(); listening = true
            task = recognizer?.recognitionTask(with: buffer) { [weak self] result, error in
                let text = result?.isFinal == true ? result?.bestTranscription.formattedString : nil
                Task { @MainActor [weak self] in
                    guard let self, self.cycle == expected, self.wanted else { return }
                    if let text {
                        self.cycle = UUID(); self.stopRecognition()
                        if let command = CookingVoiceCommand.parse(text) {
                            if command == .stop { self.stop(); return }
                            self.onCommand?(command)
                        }
                        if self.wanted && !self.reading { self.beginRecognition() }
                    } else if error != nil {
                        self.stop()
                        self.message = "Spracherkennung unterbrochen. Du kannst sie erneut einschalten."
                    }
                }
            }
        } catch {
            stop(); message = "Spracherkennung konnte nicht gestartet werden: \(error.localizedDescription)"
        }
    }
    func read(_ text: String) {
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        cycle = UUID(); stopRecognition()
        currentUtterance = nil
        speaker.stopSpeaking(at: .immediate)
        reading = true
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio)
            try AVAudioSession.sharedInstance().setActive(true)
            let speech = AVSpeechUtterance(string: text)
            speech.voice = AVSpeechSynthesisVoice(language: "de-DE")
            currentUtterance = speech
            speaker.speak(speech)
        } catch { stop(); message = error.localizedDescription }
    }
    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor [weak self] in
            guard let self, self.reading, self.currentUtterance === utterance else { return }
            self.reading = false; self.currentUtterance = nil
            if self.wanted { self.beginRecognition() }
            else { try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation) }
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        speechSynthesizer(synthesizer, didFinish: utterance)
    }
}
