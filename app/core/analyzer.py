"""

Vorher gab es einen Ollama-Pfad mit Cascade-Logik (Fast + Fallback-Modell)
plus Vision-Fallback. Aktuell nur noch OpenAI: stabil, kostengünstig
($0.15/$0.60 per 1M Tokens) und sprachunabhängig — kann englische/
italienische Captions direkt in deutsche Daten umsetzen.
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


class OpenAIAnalyzer:
    """OpenAI-API als Alternative zu Ollama.

    Stabil + multilingual + günstig.
    Nutzt Chat-Completions mit response_format=json_object.

    Kosten-Überschlag mit gpt-4o-mini (Stand 2025):
      input  $0.150 / 1M tokens
      output $0.600 / 1M tokens
    Ein Recipe-Klassifizierung braucht ~600 input + 80 output tokens.
    Bei 1000 Items/Tag ≈ $0.14 / Monat - praktisch nichts.

    Eigenschaften:
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

    def analyze_ingredients(self, description: str) -> list[dict]:
        """Wie OllamaAnalyzer.analyze_ingredients — identisches Interface,
        identisches JSON-Schema. Siehe dort für Details."""
        system = (
            "Du extrahierst Zutaten mit Mengen aus deutschsprachigen Rezept-Beschreibungen "
            "von TikTok/Instagram-Videos. Antworte AUSSCHLIESSLICH mit gültigem JSON.\n"
            "Format:\n"
            '{"ingredients":[\n'
            '  {"name":"Tomaten","amount":2,"unit":"Stück","raw":"2 große Tomaten"},\n'
            '  {"name":"Olivenöl","amount":3,"unit":"EL","raw":"3 EL Olivenöl"},\n'
            '  {"name":"Salz","amount":null,"unit":null,"raw":"Salz nach Geschmack"}\n'
            "]}\n\n"
            "Regeln:\n"
            "- amount: Zahl oder null. Bei Bereichen Mittel oder Untergrenze.\n"
            "- unit: nur aus: g, kg, ml, l, TL, EL, Stück, Prise, Bund, Zehe, "
            "Scheibe, Blatt, Pck, Dose, Tasse, Flasche, Glas. Sonst null.\n"
            "- name: nur die Zutat (ohne 'frisch', 'groß', etc.).\n"
            "- raw: genauer Text-Snippet aus der Beschreibung.\n"
            '- Bei keinen erkennbaren Zutaten: {"ingredients":[]}.'
        )
        content = self._call(system, f"Beschreibung:\n\n{description[:4000]}")
        if not content:
            return []
        try:
            data = json.loads(content)
            items = data.get("ingredients") or []
            if not isinstance(items, list):
                return []
            out = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                name = (it.get("name") or "").strip()
                if not name:
                    continue
                amount = it.get("amount")
                if amount is not None:
                    try:
                        amount = float(amount)
                    except (TypeError, ValueError):
                        amount = None
                out.append({
                    "name": name,
                    "amount": amount,
                    "unit": (it.get("unit") or None),
                    "raw": (it.get("raw") or "").strip() or None,
                })
            return out
        except Exception as e:
            logger.warning(f"OpenAI Ingredients JSON-Parse: {e} | {content[:200]}")
            return []

    def analyze_recipe_content(self, description: str) -> dict:
        """Kombinierter Call: extrahiert Zutaten + Zubereitungs-Schritte +
        Portionen-Anzahl in EINEM API-Roundtrip.

        Spart gegenüber zwei separaten Calls ~40% Tokens und ~50% Latenz
        (System-Prompt + Description müssten sonst doppelt rein). Wenn nur
        einer der Teile benötigt wird, gibt es trotzdem keinen separaten
        Endpoint — der Aufrufer pickt sich das passende Feld raus.

        Rückgabe-Schema (alle Keys immer da, aber Listen können leer / int kann null sein):
          {
            "ingredients": [{"name","amount","unit","raw"}, ...],
            "steps": [{"instruction","timer_seconds"}, ...],   # in Reihenfolge
            "servings": int | None,                            # "für 4 Personen" → 4
          }

        timer_seconds: KI schaut aktiv nach Zeit-Hinweisen im Schritt-Text
        ("8 Min köcheln", "20 Minuten backen", "über Nacht ziehen lassen").
        Bei "über Nacht" o.ä. setzt sie NULL (kein realistischer Stoppuhr-Wert).
        """
        system = (
            "Du analysierst deutschsprachige Rezept-Beschreibungen von TikTok/Instagram-Videos "
            "und extrahierst Zutaten, Zubereitungs-Schritte und Portionen-Anzahl. "
            "Antworte AUSSCHLIESSLICH mit gültigem JSON nach diesem Schema:\n"
            '{"ingredients":[\n'
            '  {"name":"Tomaten","amount":2,"unit":"Stück","raw":"2 große Tomaten"},\n'
            '  ...\n'
            "],\n"
            '"steps":[\n'
            '  {"instruction":"Wasser in einen Topf geben und zum Kochen bringen.","timer_seconds":null},\n'
            '  {"instruction":"Spaghetti hinzugeben und 8 Minuten kochen.","timer_seconds":480},\n'
            '  {"instruction":"In der Zwischenzeit die Tomaten würfeln.","timer_seconds":null}\n'
            "],\n"
            '"servings":4\n'
            "}\n\n"
            "REGELN ZUTATEN:\n"
            "- amount: Zahl oder null. Bei Bereichen ('2-3 Eier') Mittel oder Untergrenze.\n"
            "- unit: nur aus: g, kg, ml, l, TL, EL, Stück, Prise, Bund, Zehe, Scheibe, "
            "Blatt, Pck, Dose, Tasse, Flasche, Glas. Sonst null.\n"
            "- name: nur die Zutat (ohne 'frisch', 'groß', etc.).\n"
            "- raw: genauer Text-Snippet aus der Beschreibung.\n\n"
            "REGELN SCHRITTE:\n"
            "- instruction: vollständiger deutscher Satz, max 200 Zeichen. Stelle Werkzeug "
            "und Zutat-Bezug klar.\n"
            "- timer_seconds: NUR setzen wenn ein konkreter, einkalkulierbarer Zeitwert da ist. "
            "'8 Min köcheln' → 480. '20 Minuten backen' → 1200. "
            "'kurz anbraten', 'goldbraun', 'über Nacht ziehen', 'bis fertig' → null.\n"
            "- Reihenfolge muss der Zubereitung entsprechen.\n\n"
            "REGELN PORTIONEN:\n"
            "- servings: Anzahl Portionen (1-12) wenn explizit ('für 4 Personen', 'ergibt 8 Stück'). "
            "Sonst null. Nicht raten.\n\n"
            "Bei nicht-rezept-artigem Text (z.B. reine Caption ohne Anleitung): "
            '{"ingredients":[],"steps":[],"servings":null}.'
        )
        content = self._call(system, f"Beschreibung:\n\n{description[:6000]}")
        if not content:
            return {"ingredients": [], "steps": [], "servings": None}
        try:
            data = json.loads(content)
            # Ingredients
            ings_out = []
            for it in (data.get("ingredients") or []):
                if not isinstance(it, dict):
                    continue
                name = (it.get("name") or "").strip()
                if not name:
                    continue
                amount = it.get("amount")
                if amount is not None:
                    try:
                        amount = float(amount)
                    except (TypeError, ValueError):
                        amount = None
                ings_out.append({
                    "name": name,
                    "amount": amount,
                    "unit": (it.get("unit") or None),
                    "raw": (it.get("raw") or "").strip() or None,
                })
            # Steps
            steps_out = []
            for s in (data.get("steps") or []):
                if not isinstance(s, dict):
                    continue
                instr = (s.get("instruction") or "").strip()
                if not instr:
                    continue
                timer = s.get("timer_seconds")
                if timer is not None:
                    try:
                        timer = int(timer)
                        if timer <= 0 or timer > 86400:  # > 24h = unsinnig für Stoppuhr
                            timer = None
                    except (TypeError, ValueError):
                        timer = None
                steps_out.append({"instruction": instr, "timer_seconds": timer})
            # Servings
            servings = data.get("servings")
            if servings is not None:
                try:
                    servings = int(servings)
                    if servings < 1 or servings > 50:  # Sanity
                        servings = None
                except (TypeError, ValueError):
                    servings = None
            return {"ingredients": ings_out, "steps": steps_out, "servings": servings}
        except Exception as e:
            logger.warning(f"OpenAI Recipe-Content JSON-Parse: {e} | {content[:200]}")
            return {"ingredients": [], "steps": [], "servings": None}

    def translate_to_german(self, text: str) -> Optional[str]:
        """Erkennt Sprache und übersetzt nach Deutsch falls nötig.

        Returns:
            - None, wenn der Text bereits deutsch ist (kein Translate nötig)
            - None, wenn der Text zu kurz/leer ist oder die KI fehlschlägt
              (Aufrufer behält dann das Original — sicherer als Crash)
            - Den übersetzten deutschen Text, wenn Original nicht-deutsch war.

        Strategie: ein einziger Call mit response_format=json_object und
        Schema {is_german: bool, translation: string|null}. So spart man
        sich die Sprach-Erkennung als separaten Call.
        """
        if not text or len(text.strip()) < 20:
            return None
        system = (
            "Du erkennst die Sprache eines Texts und übersetzt ihn nach Deutsch "
            "falls nötig. Erhalte Emojis, Hashtags und Marker wie '@user' wie sie sind. "
            "Antworte AUSSCHLIESSLICH mit gültigem JSON in einem dieser zwei Formate:\n"
            '  {"is_german": true}                  // wenn Text bereits deutsch\n'
            '  {"is_german": false, "translation": "..."}  // mit deutscher Übersetzung\n'
            "Wenn der Text gemischt-sprachig ist (z.B. deutscher Untertitel + englischer "
            "Hashtag-Schwanz), gilt er als 'is_german: true' wenn der inhaltliche Kern "
            "deutsch ist."
        )
        content = self._call(system, f"Text:\n\n{text[:4000]}")
        if not content:
            return None
        try:
            data = json.loads(content)
            if data.get("is_german"):
                return None
            translation = (data.get("translation") or "").strip()
            return translation or None
        except Exception as e:
            logger.warning(f"OpenAI Translate JSON-Parse: {e} | {content[:200]}")
            return None


def build_analyzer(ai_cfg: dict):
    """Factory die einen OpenAI-Analyzer baut.

    Seit Ollama-Removal gibt es keinen Provider-Switch mehr - der ai.provider-
    Key in der Config wird ignoriert (toleriert für alte configs).

    Beispiel-Config:
      ai:
        confidence_threshold: 0.85
        description_min_length: 20
        openai:
          api_key: sk-...
          model: gpt-4o-mini
          base_url: ""   # optional, für Azure/OpenRouter/etc.
          timeout: 30
    """
    oa = ai_cfg.get("openai") or {}
    api_key = (oa.get("api_key") or "").strip()
    if not api_key:
        raise ValueError(
            "OpenAI api_key fehlt in der Config. Siehe Einstellungen → AI."
        )
    # Defensiver Check: wenn das Mask-Konstante "********" o.ä. aus der
    # Config kommt (Konfigurations-Bug), nicht versuchen damit einen
    # Analyzer zu bauen - das gibt sonst beim ersten Call HTTP 401 von
    # OpenAI und der Scraper bricht ab.
    if api_key == "********" or set(api_key) <= {"*", "•"}:
        raise ValueError(
            "OpenAI api_key in Config sieht wie die UI-Maske aus "
            "('********' oder ähnlich) - bitte echten Key eintragen "
            "und speichern. Beim nächsten Page-Reload zeigt die UI "
            "den Key wieder maskiert an, das ist nur die Anzeige - "
            "die Config hat aber den echten Wert."
        )
    return OpenAIAnalyzer(
        api_key=api_key,
        model=(oa.get("model") or "gpt-4o-mini").strip(),
        base_url=(oa.get("base_url") or "").strip() or None,
        timeout=int(oa.get("timeout") or 30),
    )
