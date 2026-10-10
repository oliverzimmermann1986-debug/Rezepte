# Zustimmung zu angeforderten KI-Aktionen

Web und iPhone erklären vor einer Aktion den Empfänger OpenAI, die übertragenen Daten und den Zweck einschließlich möglicher Import-Folgeschritte. Die Freigabe wird nicht dauerhaft in Browser, Keychain oder Einstellungen gespeichert. Native Freigaben sind an Sitzung, Server, Aktion und eine einmal verwendbare Kennung gebunden. Abbrechen startet keinen API-Aufruf. Geteilte Links werden zunächst nur lokal vorgemerkt.

Der Server erwartet `ai_processing_consent: "openai-recipe-v1"` im JSON-Body bzw. als Multipart-Feld; der ausdrücklich gestartete KI-Audit verwendet einen Queryparameter. Fehlende oder falsche Freigabe ergibt HTTP 428 (`AI_CONSENT_REQUIRED`). Reine Lesezugriffe und lokale Aktionen benötigen keine KI-Freigabe. Pending-Speichern benötigt sie wegen möglicher Extraktion, Übersetzung und Bilderzeugung; Verwerfen nicht. PDF-Verarbeitung benötigt sie bei aktivierter Rezeptanalyse, auch im Probelauf (Standard: aktiviert).

Die Freigabe gilt für OpenAI unter `https://api.openai.com/v1`. Andere kompatible API-Ziele ergeben HTTP 409 (`AI_PROVIDER_UNSUPPORTED`), da der angezeigte Empfänger sonst falsch wäre. Persistente Import-/Bildaufträge speichern die Version und prüfen sie vor Ausführung erneut. Bestehende Aufträge ohne Freigabe werden nicht automatisch nachgenehmigt: Die betreffende Aktion muss erneut bestätigt werden. HTTP- und Thread-Kontexte tragen die Freigabe nur für den jeweiligen Auftrag weiter.

Rezeptlisten starten keine Hintergrundanalyse mehr. Explizite Extraktionen erfassen die freigegebenen IDs und erhalten beim Warten jeweils ihren eigenen Haushaltskontext. Bilder-Sammelläufe speichern ihre Ziel-IDs bereits beim bestätigten Request. Die serverseitige Markierung ersetzt weder Authentifizierung noch Rollen- und Haushaltsprüfung. Ein selbst gebauter API-Client muss vor dem Senden dieses Feldes eine entsprechende eigene Einwilligungsoberfläche bereitstellen; der Server kann einen dargestellten Dialog nicht kryptographisch beweisen.

Für vorhandene Share-Kurzbefehle gilt derselbe Vertrag: Token-Authentifizierung bleibt erforderlich, zusätzlich muss vor jedem Import die beschriebene Zustimmung erfolgen. Eine dauerhaft vorbelegte Zustimmung im Kurzbefehl ist nicht vorgesehen. Separat vom Betreiber konfigurierte Dienste sind kein dauerhaftes Einverständnis eines App-Nutzers.

Prüfungen: `tests/test_ai_consent.py`, `tests/web_ai_consent.test.cjs`, `ios-swift/RezepteTests/AIProcessingConsentTests.swift`. Die Tests verwenden synthetische Daten und Testtransporte, keine echten KI-Anfragen.
