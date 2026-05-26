"""KI-Analyse: Ollama-only (Fast-Modell + optional Fallback-Modell).

OpenAI Vision wurde entfernt - die Cascade besteht nur noch aus zwei
Ollama-Calls. Wenn auch der Fallback unsicher ist, landet das Item in
Pending und der User entscheidet manuell im Web-UI.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class RecipeAnalysis:
    name: str
    type: str
    category: Optional[str]
    confidence: float
    is_manual: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "RecipeAnalysis":
        return cls(
            name=data.get("rezeptname") or data.get("name") or "Unbekannt",
            type=data.get("typ") or data.get("type") or "Unbekannt",
            category=data.get("kategorie") or data.get("category"),
            confidence=float(data.get("confidence", 0)),
        )

    def needs_manual_input(self, threshold: float) -> bool:
        return (
            self.confidence < threshold
            or self.name.lower() == "unbekannt"
            or self.type.lower() == "unbekannt"
        )


@dataclass
class WeddingAnalysis:
    name: str
    category: Optional[str]
    confidence: float
    is_manual: bool = False

    def needs_manual_input(self, threshold: float) -> bool:
        return self.confidence < threshold or self.name.lower() == "unbekannt"


class OllamaAnalyzer:
    def __init__(self, url: str, model: str, timeout: int = 60):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout
        # Connection-Reuse: spart pro Call ~20-50ms TCP-Handshake.
        # Bei 50 URLs × 2 Modelle = 100 Calls/Run macht das spürbar.
        self.session = requests.Session()

    def _call(self, system: str, user: str) -> Optional[str]:
        try:
            r = self.session.post(
                f"{self.url}/api/generate",
                json={
                    "model": self.model,
                    "system": system,
                    "prompt": user,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.2, "num_predict": 250},
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
            return r.json().get("response", "").strip()
        except Exception as e:
            logger.error(f"Ollama Call: {e}")
            return None

    def health(self) -> bool:
        try:
            r = self.session.get(f"{self.url}/api/tags", timeout=5)
            r.raise_for_status()
            models = [m.get("name", "") for m in r.json().get("models", [])]
            return any(self.model in m for m in models)
        except Exception:
            return False

    def analyze_recipe(self, description: str) -> RecipeAnalysis:
        system = (
            "Du analysierst TikTok/Instagram-Beschreibungen von Rezept-Videos. "
            "Extrahiere Rezeptname, Typ (Hauptgericht, Vorspeise, Nachspeise, Snack, "
            "Frühstück, Getränk, Beilage) und Unterkategorie (z.B. Pasta, Fleisch, Fisch, "
            "Vegetarisch, Vegan, Kuchen, Suppe). Antworte AUSSCHLIESSLICH mit gültigem JSON: "
            '{"rezeptname":"...","typ":"...","kategorie":"...","confidence":0.85}. '
            "Bei Unsicherheit nutze 'Unbekannt'."
        )
        content = self._call(system, f"Beschreibung:\n\n{description[:2000]}")
        if not content:
            return RecipeAnalysis("Unbekannt", "Unbekannt", None, 0.0)
        try:
            return RecipeAnalysis.from_dict(json.loads(content))
        except Exception as e:
            logger.warning(f"Ollama JSON-Parse: {e} | {content[:120]}")
            return RecipeAnalysis("Unbekannt", "Unbekannt", None, 0.0)

    def analyze_wedding(self, description: str, categories: list[str]) -> WeddingAnalysis:
        cats = ", ".join(categories)
        system = (
            "Du analysierst TikTok/Instagram-Beschreibungen von Hochzeits-Content "
            f"(Deko, Foto, Basteln, Einladung, etc.). Mögliche Kategorien: {cats}. "
            "Erstelle einen kurzen deutschen Namen (max 5 Wörter) UND wähle die passendste "
            "Kategorie aus der Liste. Antworte AUSSCHLIESSLICH mit gültigem JSON: "
            '{"name":"Kurzer Name","kategorie":"Deko","confidence":0.85}. '
            "Bei Unsicherheit 'Unbekannt' / 'Sonstiges'."
        )
        content = self._call(system, f"Beschreibung:\n\n{description[:2000]}")
        if not content:
            return WeddingAnalysis("Unbekannt", None, 0.0)
        try:
            data = json.loads(content)
            return WeddingAnalysis(
                name=data.get("name") or "Unbekannt",
                category=data.get("kategorie") or data.get("category"),
                confidence=float(data.get("confidence", 0)),
            )
        except Exception as e:
            logger.warning(f"Ollama Wedding JSON-Parse: {e} | {content[:120]}")
            return WeddingAnalysis("Unbekannt", None, 0.0)

    def spellcheck(self, name: str, typ: str, category: str) -> dict:
        system = (
            "Du bist ein deutscher Rechtschreib-Korrektor. Korrigiere nur Tippfehler, "
            "ändere NICHT die Bedeutung. Antworte mit JSON: "
            '{"name":"...","type":"...","category":"..."}'
        )
        content = self._call(system, f"Korrigiere: Name={name}, Typ={typ}, Kategorie={category}")
        if not content:
            return {"name": name, "type": typ, "category": category}
        try:
            d = json.loads(content)
            return {
                "name": d.get("name") or name,
                "type": d.get("type") or typ,
                "category": d.get("category") or category,
            }
        except Exception:
            return {"name": name, "type": typ, "category": category}


class OpenAIAnalyzer:
    """OpenAI-API als Alternative zu Ollama.

    Gleicher Public-API wie OllamaAnalyzer - kann 1:1 als drop-in
    ersetzt werden. Nutzt Chat-Completions mit response_format=json_object.

    Kosten-Überschlag mit gpt-4o-mini (Stand 2025):
      input  $0.150 / 1M tokens
      output $0.600 / 1M tokens
    Ein Recipe-Klassifizierung braucht ~600 input + 80 output tokens.
    Bei 1000 Items/Tag ≈ $0.14 / Monat - praktisch nichts.

    Vorteile gegenüber Ollama:
      + Bessere Klassifikation (gpt-4o-mini schlägt Qwen 7B)
      + Keine GPU/RAM-Anforderungen im Container
      + Schneller (~500ms statt ~2-3s pro Call)
    Nachteile:
      - Kostet Geld (winzig aber existent)
      - Externe Dependency (Internet, OpenAI-Outage)
      - Daten gehen an OpenAI
    """

    OPENAI_BASE = "https://api.openai.com/v1"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", *,
                 base_url: Optional[str] = None, timeout: int = 30):
        if not api_key:
            raise ValueError("OpenAI api_key fehlt")
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or self.OPENAI_BASE).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })

    def _call(self, system: str, user: str) -> Optional[str]:
        try:
            r = self.session.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2,
                    "max_tokens": 300,
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
            choices = data.get("choices") or []
            if not choices:
                return None
            return (choices[0].get("message") or {}).get("content", "").strip()
        except requests.exceptions.HTTPError as e:
            # 401/403/429 sind häufig und sollten verständlich loggen
            try:
                body = e.response.json().get("error", {}).get("message", "")
            except Exception:
                body = e.response.text[:200] if e.response is not None else ""
            logger.error(f"OpenAI HTTP {e.response.status_code if e.response else '?'}: {body}")
            return None
        except Exception as e:
            logger.error(f"OpenAI Call: {e}")
            return None

    def health(self) -> bool:
        """Pingt GET /v1/models. 401 = Key falsch, 200 = ok.
        Loggt den konkreten Grund bei Fehler - sonst sieht der User nur
        'AI nicht erreichbar' ohne zu wissen ob Key, Netz oder Account-Problem."""
        try:
            r = self.session.get(f"{self.base_url}/models", timeout=15)
            if r.status_code == 200:
                return True
            if r.status_code == 401:
                logger.error("OpenAI health: API-Key ungültig (HTTP 401)")
            elif r.status_code == 403:
                logger.error("OpenAI health: Zugriff verweigert (HTTP 403) - Account/Billing prüfen")
            elif r.status_code == 429:
                logger.error("OpenAI health: Rate limit (HTTP 429)")
            else:
                logger.error(f"OpenAI health: HTTP {r.status_code} - {r.text[:200]}")
            return False
        except requests.exceptions.Timeout:
            logger.error(f"OpenAI health: Timeout (15s) gegen {self.base_url}/models - Internet vom Container aus?")
            return False
        except requests.exceptions.ConnectionError as e:
            logger.error(f"OpenAI health: keine Verbindung zu {self.base_url}: {e}")
            return False
        except Exception as e:
            logger.error(f"OpenAI health: unerwarteter Fehler: {e}")
            return False

    def analyze_recipe(self, description: str) -> RecipeAnalysis:
        system = (
            "Du analysierst TikTok/Instagram-Beschreibungen von Rezept-Videos. "
            "Extrahiere Rezeptname, Typ (Hauptgericht, Vorspeise, Nachspeise, Snack, "
            "Frühstück, Getränk, Beilage) und Unterkategorie (z.B. Pasta, Fleisch, Fisch, "
            "Vegetarisch, Vegan, Kuchen, Suppe). Antworte AUSSCHLIESSLICH mit gültigem JSON: "
            '{"rezeptname":"...","typ":"...","kategorie":"...","confidence":0.85}. '
            "Bei Unsicherheit nutze 'Unbekannt'."
        )
        content = self._call(system, f"Beschreibung:\n\n{description[:2000]}")
        if not content:
            return RecipeAnalysis("Unbekannt", "Unbekannt", None, 0.0)
        try:
            return RecipeAnalysis.from_dict(json.loads(content))
        except Exception as e:
            logger.warning(f"OpenAI JSON-Parse: {e} | {content[:120]}")
            return RecipeAnalysis("Unbekannt", "Unbekannt", None, 0.0)

    def analyze_wedding(self, description: str, categories: list[str]) -> WeddingAnalysis:
        cats = ", ".join(categories)
        system = (
            "Du analysierst TikTok/Instagram-Beschreibungen von Hochzeits-Content "
            f"(Deko, Foto, Basteln, Einladung, etc.). Mögliche Kategorien: {cats}. "
            "Erstelle einen kurzen deutschen Namen (max 5 Wörter) UND wähle die passendste "
            "Kategorie aus der Liste. Antworte AUSSCHLIESSLICH mit gültigem JSON: "
            '{"name":"Kurzer Name","kategorie":"Deko","confidence":0.85}. '
            "Bei Unsicherheit 'Unbekannt' / 'Sonstiges'."
        )
        content = self._call(system, f"Beschreibung:\n\n{description[:2000]}")
        if not content:
            return WeddingAnalysis("Unbekannt", None, 0.0)
        try:
            data = json.loads(content)
            return WeddingAnalysis(
                name=data.get("name") or "Unbekannt",
                category=data.get("kategorie") or data.get("category"),
                confidence=float(data.get("confidence", 0)),
            )
        except Exception as e:
            logger.warning(f"OpenAI Wedding JSON-Parse: {e} | {content[:120]}")
            return WeddingAnalysis("Unbekannt", None, 0.0)

    def spellcheck(self, name: str, typ: str, category: str) -> dict:
        system = (
            "Du bist ein deutscher Rechtschreib-Korrektor. Korrigiere nur Tippfehler, "
            "ändere NICHT die Bedeutung. Antworte mit JSON: "
            '{"name":"...","type":"...","category":"..."}'
        )
        content = self._call(system, f"Korrigiere: Name={name}, Typ={typ}, Kategorie={category}")
        if not content:
            return {"name": name, "type": typ, "category": category}
        try:
            d = json.loads(content)
            return {
                "name": d.get("name") or name,
                "type": d.get("type") or typ,
                "category": d.get("category") or category,
            }
        except Exception:
            return {"name": name, "type": typ, "category": category}


def build_analyzer(ai_cfg: dict):
    """Factory die je nach Config einen Analyzer baut.

    Provider-Modi:
      - 'ollama'  (Default für Bestandskonfigurationen, lokales LLM)
      - 'openai'  (Cloud, gpt-4o-mini etc.)

    Beispiel-Config:
      ai:
        provider: openai
        confidence_threshold: 0.85
        fallback_threshold: 0.5
        openai:
          api_key: sk-...
          model: gpt-4o-mini
          base_url: ""   # optional, für Azure/OpenRouter/etc.
        ollama:
          url: http://host.docker.internal:11434
          model: qwen2.5:7b-instruct
    """
    provider = (ai_cfg.get("provider") or "ollama").lower().strip()

    if provider == "openai":
        oa = ai_cfg.get("openai") or {}
        return OpenAIAnalyzer(
            api_key=(oa.get("api_key") or "").strip(),
            model=(oa.get("model") or "gpt-4o-mini").strip(),
            base_url=(oa.get("base_url") or "").strip() or None,
            timeout=int(oa.get("timeout") or 30),
        )

    # Default: Ollama
    ol = ai_cfg.get("ollama") or {}
    return OllamaAnalyzer(
        url=(ol.get("url") or "http://localhost:11434").strip(),
        model=(ol.get("model") or "qwen2.5:7b-instruct").strip(),
        timeout=int(ol.get("timeout") or 60),
    )
