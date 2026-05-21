"""KI-Analyse: Ollama (primär, lokal) + OpenAI Vision (Fallback)."""
from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
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


def _strip_json_fences(text: str) -> str:
    return re.sub(r"^```json\s*|\s*```$", "", text, flags=re.MULTILINE).strip()


class OllamaAnalyzer:
    def __init__(self, url: str, model: str, timeout: int = 60):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _call(self, system: str, user: str) -> Optional[str]:
        try:
            r = requests.post(
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
            r = requests.get(f"{self.url}/api/tags", timeout=5)
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
            return RecipeAnalysis.from_dict(json.loads(_strip_json_fences(content)))
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
            data = json.loads(_strip_json_fences(content))
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
            d = json.loads(_strip_json_fences(content))
            return {
                "name": d.get("name") or name,
                "type": d.get("type") or typ,
                "category": d.get("category") or category,
            }
        except Exception:
            return {"name": name, "type": typ, "category": category}


class OpenAIVisionAnalyzer:
    """Nur wenn Beschreibung leer/zu kurz."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        # Lazy import - nur wenn benötigt
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key)
        self.model = model

    @staticmethod
    def _b64(image_path: Path) -> str:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode()

    def analyze_recipe(self, image: Path) -> RecipeAnalysis:
        try:
            b64 = self._b64(image)
            r = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": (
                        'Antworte NUR mit JSON: {"rezeptname":"...","typ":"...",'
                        '"kategorie":"...","confidence":0.85}')},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Was ist das für ein Rezept?"},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}"}},
                    ]},
                ],
                temperature=0.2, max_tokens=150,
            )
            txt = _strip_json_fences(r.choices[0].message.content)
            return RecipeAnalysis.from_dict(json.loads(txt))
        except Exception as e:
            logger.error(f"OpenAI Vision Rezept: {e}")
            return RecipeAnalysis("Unbekannt", "Unbekannt", None, 0.0)

    def analyze_wedding(self, image: Path, categories: list[str]) -> WeddingAnalysis:
        try:
            b64 = self._b64(image)
            cats = ", ".join(categories)
            r = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": (
                        f"Hochzeits-Bild analysieren. Kategorien: {cats}. "
                        'Antworte NUR mit JSON: {"name":"...","kategorie":"...","confidence":0.85}')},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Was zeigt dieses Bild?"},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}"}},
                    ]},
                ],
                temperature=0.2, max_tokens=120,
            )
            txt = _strip_json_fences(r.choices[0].message.content)
            d = json.loads(txt)
            return WeddingAnalysis(
                name=d.get("name") or "Unbekannt",
                category=d.get("kategorie") or d.get("category"),
                confidence=float(d.get("confidence", 0)),
            )
        except Exception as e:
            logger.error(f"OpenAI Vision Hochzeit: {e}")
            return WeddingAnalysis("Unbekannt", None, 0.0)
