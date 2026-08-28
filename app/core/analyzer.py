"""

Vorher gab es einen Ollama-Pfad mit Cascade-Logik (Fast + Fallback-Modell)
plus Vision-Fallback. Aktuell nur noch OpenAI: stabil, kostengünstig
($0.15/$0.60 per 1M Tokens) und sprachunabhängig — kann englische/
italienische Captions direkt in deutsche Daten umsetzen.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import List, Optional

import requests

from .webhook import server_configured_request

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
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Einziger OpenAI-Transportpfad für öffentliche und interne Ziele."""
        normalized_path = "/" + (path or "").lstrip("/")
        headers = dict(self._headers)
        # requests setzt für Multipart-Uploads den Content-Type inklusive
        # Boundary selbst. Ein festes application/json macht Audio-Uploads
        # sonst für die Transcriptions-API unlesbar.
        if kwargs.get("files"):
            headers.pop("Content-Type", None)
        headers.update(kwargs.pop("headers", {}) or {})
        return server_configured_request(
            method,
            f"{self.base_url}{normalized_path}",
            trusted_private_bases=(self.base_url,),
            headers=headers,
            **kwargs,
        )

    @staticmethod
    def _retry_delay(response: requests.Response, attempt: int) -> float:
        """Liest OpenAI-Wartehinweise und begrenzt das Backoff."""
        raw = response.headers.get("retry-after-ms")
        if raw:
            try:
                return max(1.5, min(12.0, float(raw) / 1000.0 + 0.5))
            except (TypeError, ValueError):
                pass
        raw = response.headers.get("retry-after")
        if raw:
            try:
                return max(1.5, min(12.0, float(raw) + 0.5))
            except (TypeError, ValueError):
                pass
        match = re.search(
            r"try again in\s+([0-9]+(?:\.[0-9]+)?)\s*(ms|s)",
            response.text or "",
            flags=re.IGNORECASE,
        )
        if match:
            value = float(match.group(1))
            if match.group(2).lower() == "ms":
                value /= 1000.0
            return max(1.5, min(12.0, value + 0.5))
        return min(12.0, 1.5 * (2 ** attempt))

    def _request_with_retry(self, method: str, path: str, **kwargs) -> requests.Response:
        """Wiederholt ausschließlich temporäre API-Antworten mit kurzem Backoff."""
        retryable = {429, 500, 502, 503, 504}
        attempts = 6
        response: Optional[requests.Response] = None
        for attempt in range(attempts):
            try:
                response = self.request(method, path, **kwargs)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                if attempt == attempts - 1:
                    raise
                delay = min(12.0, 1.5 * (2 ** attempt))
                logger.warning(
                    "OpenAI Transportfehler %s, Wiederholung %s/%s in %.2fs",
                    type(exc).__name__, attempt + 1, attempts - 1, delay,
                )
                time.sleep(delay)
                for value in (kwargs.get("files") or {}).values():
                    handle = value[1] if isinstance(value, tuple) and len(value) > 1 else value
                    try:
                        handle.seek(0)
                    except (AttributeError, OSError):
                        pass
                continue
            if response.status_code not in retryable or attempt == attempts - 1:
                return response
            delay = self._retry_delay(response, attempt)
            logger.warning(
                "OpenAI HTTP %s, Wiederholung %s/%s in %.2fs",
                response.status_code,
                attempt + 1,
                attempts - 1,
                delay,
            )
            time.sleep(delay)
            # Multipart-Dateihandles wurden beim vorherigen Versuch gelesen.
            for value in (kwargs.get("files") or {}).values():
                handle = value[1] if isinstance(value, tuple) and len(value) > 1 else value
                try:
                    handle.seek(0)
                except (AttributeError, OSError):
                    pass
        assert response is not None
        return response

    def generate_recipe_image(
        self,
        prompt: str,
        *,
        model: str = "gpt-image-2",
        size: str = "1536x1024",
        quality: str = "medium",
        output_format: str = "jpeg",
    ) -> bytes:
        """Erzeugt ein einzelnes Rezeptbild über die OpenAI Image API."""
        clean_prompt = " ".join(str(prompt or "").split())
        if not clean_prompt:
            raise ValueError("Bild-Prompt fehlt")
        if size not in {"1024x1024", "1536x1024", "1024x1536", "auto"}:
            raise ValueError("Ungültige Bildgröße")
        if quality not in {"low", "medium", "high", "auto"}:
            raise ValueError("Ungültige Bildqualität")
        if output_format not in {"jpeg", "png", "webp"}:
            raise ValueError("Ungültiges Bildformat")
        response = self._request_with_retry(
            "POST",
            "/images/generations",
            json={
                "model": (model or "gpt-image-2").strip(),
                "prompt": clean_prompt[:32000],
                "n": 1,
                "size": size,
                "quality": quality,
                "output_format": output_format,
            },
            timeout=max(180, int(self.timeout)),
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        encoded = data[0].get("b64_json") if isinstance(data, list) and data else None
        if not isinstance(encoded, str) or not encoded:
            raise RuntimeError("OpenAI Image API lieferte keine Bilddaten")
        try:
            image = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError("OpenAI Image API lieferte ungültige Bilddaten") from exc
        if not image or len(image) > 25 * 1024 * 1024:
            raise RuntimeError("Generiertes Bild ist leer oder größer als 25 MB")
        return image

    def _call(self, system: str, user: str) -> Optional[str]:
        # max_tokens=6000: bei gpt-4o-mini gibt's 16k Output-Limit, 6k ist
        # also überdimensioniert. Output-Kosten bei mini sind ~$0.0006/1k,
        # also auch bei 6k nur ~$0.004 worst-case pro Call — nicht relevant.
        # Vorher: 300 → 2000, beides zu wenig für lange Rezepte mit vielen
        # Schritten. Logging unten verrät wie groß die Antworten wirklich sind.
        try:
            r = self._request_with_retry(
                "POST",
                "/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2,
                    "max_tokens": 6000,
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
            choices = data.get("choices") or []
            if not choices:
                logger.warning("_call: keine choices in API-Response")
                return None
            finish = choices[0].get("finish_reason")
            content = (choices[0].get("message") or {}).get("content", "").strip()

            # Diagnose-Log: hilft beim Debugging warum Rezepte leer bleiben.
            # input/output-Token zeigen ob die KI gar nicht erst antwortet
            # oder die Antwort zu lang wurde.
            usage = data.get("usage") or {}
            logger.info(
                f"_call: in={usage.get('prompt_tokens', '?')}t, "
                f"out={usage.get('completion_tokens', '?')}t, "
                f"finish={finish}, content_len={len(content)}"
            )

            if finish == "length":
                # Auch bei 6k erreicht? Dann ist was kaputt (Endlos-Loop in KI etc).
                logger.warning(
                    f"_call: max_tokens=6000 erreicht (finish_reason=length) — "
                    f"JSON unvollständig. content[-200:]={content[-200:]!r}"
                )
                return None
            if not content:
                logger.warning(f"_call: leerer content trotz finish={finish}")
                return None
            return content
        except requests.exceptions.HTTPError as e:
            # 401/403/429 sind häufig und sollten verständlich loggen
            logger.error(
                "OpenAI HTTP %s: %s",
                e.response.status_code if e.response is not None else "?",
                (e.response.text or "")[:300] if e.response is not None else str(e),
            )
            return None
        except Exception as e:
            logger.error(f"OpenAI Call: {e}")
            return None

    def extract_description_from_media(self, file_path) -> Optional[str]:
        """Extrahiert Rezept-Beschreibung aus PDF oder Bild, wenn keine .txt-
        Datei im Folder ist. Wird vom Indexer als zweiter Fallback aufgerufen.

        - PDF: pdfplumber text-extraction (lokal, gratis). Wenn Text-leer
          (gescanntes PDF) → None (Vision-Fallback wäre möglich, braucht aber
          poppler-utils oder pdf2image).
        - Bild (jpg/png/webp): OpenAI Vision-Call mit gpt-4o-mini. ~1500 Tokens
          input + 500 output = ~$0.001-0.003 pro Bild.

        Returns: gefundener Text (gestrippt, joined newlines) oder None.
        """
        from pathlib import Path as _P
        p = _P(file_path)
        if not p.exists():
            return None
        suffix = p.suffix.lower()

        if suffix == ".pdf":
            try:
                import pdfplumber
                with pdfplumber.open(str(p)) as pdf:
                    parts = []
                    for page in pdf.pages:
                        t = page.extract_text()
                        if t:
                            parts.append(t)
                    txt = "\n\n".join(parts).strip()
                if len(txt) >= 20:
                    logger.info(f"PDF-Text extrahiert: {p.name} ({len(txt)} chars)")
                    return txt
                # PDF hat keinen Text-Layer (Scan) → render zu PNG + Vision-Call
                logger.info(f"PDF {p.name}: kein Text-Layer, Vision-Fallback")
                return self._extract_pdf_via_vision(p)
            except Exception as e:
                logger.warning(f"pdfplumber-Fehler bei {p}: {e}")
                return None

        if suffix in (".jpg", ".jpeg", ".png", ".webp"):
            try:
                mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else f"image/{suffix.lstrip('.')}"
                txt = self.extract_description_from_image_bytes(p.read_bytes(), mime)
                if txt:
                    logger.info(f"Vision-Extract: {p.name} ({len(txt)} chars)")
                else:
                    logger.info(f"Vision {p.name}: kein verwertbarer Text")
                return txt
            except Exception as e:
                logger.warning(f"Vision-Extract-Fehler bei {p}: {e}")
                return None

        return None

    def extract_description_from_image_bytes(
        self,
        image_bytes: bytes,
        mime_type: str,
        context: str = "",
    ) -> Optional[str]:
        """Liest ein manuell hochgeladenes Rezeptbild sofort per Vision aus.

        Anders als die reine Bild-Klassifizierung liefert diese Methode den
        vollständigen erkannten Rezepttext. Dieser kann anschließend durch die
        normale Zutaten-/Schritt-Pipeline laufen, ohne dass zuerst ein
        Dateisystem-Sync oder ein späterer Hintergrundlauf nötig ist.
        """
        if not image_bytes:
            return None
        import base64

        normalized_mime = (mime_type or "image/jpeg").lower()
        image_format = normalized_mime.removeprefix("image/")
        if image_format == "jpg":
            image_format = "jpeg"
        prompt = (
            "Du siehst ein Bild eines Rezepts (Foto, Screenshot oder Rezeptkarte). "
            "Extrahiere ALLEN lesbaren Rezepttext und gib eine zusammenhängende "
            "deutsche Rezept-Beschreibung zurück, mit Zutaten samt Mengen sowie "
            "Zubereitungs-Schritten. Erfinde keine unlesbaren Angaben. Wenn nichts "
            "Rezept-Artiges lesbar ist, antworte exakt mit: KEINE_REZEPT_DATEN"
        )
        if context.strip():
            prompt += f"\nDateiname oder Kontext: {context.strip()[:200]}"
        b64 = base64.b64encode(image_bytes).decode("ascii")
        text = self._call_vision(b64, image_format, prompt)
        if not text or "KEINE_REZEPT_DATEN" in text or len(text.strip()) < 20:
            return None
        return text.strip()

    def extract_text_from_video_frame_bytes(
        self,
        image_bytes: bytes,
        mime_type: str = "image/jpeg",
        context: str = "",
    ) -> Optional[str]:
        """Liest ausschließlich sichtbaren Rezepttext aus einem Videoframe.

        Die engere Anweisung verhindert, dass aus dem gezeigten Essen Zutaten
        geraten werden. Mehrere Frames werden später dedupliziert und gemeinsam
        durch die normale strukturierte Rezeptanalyse geschickt.
        """
        if not image_bytes:
            return None
        import base64

        normalized_mime = (mime_type or "image/jpeg").lower()
        image_format = normalized_mime.removeprefix("image/")
        if image_format == "jpg":
            image_format = "jpeg"
        prompt = (
            "Lies ausschließlich den im Videoframe sichtbar eingeblendeten Text. "
            "Übernimm Zutaten, Mengen, Zeiten und Zubereitungshinweise exakt, "
            "soweit sie lesbar sind. Erfinde nichts aus dem gezeigten Essen oder "
            "aus Personen im Bild. Wenn kein verwertbarer Rezepttext sichtbar ist, "
            "antworte exakt mit: KEINE_REZEPT_DATEN"
        )
        if context.strip():
            prompt += f"\nKontext: {context.strip()[:200]}"
        text = self._call_vision(
            base64.b64encode(image_bytes).decode("ascii"),
            image_format,
            prompt,
        )
        if not text or "KEINE_REZEPT_DATEN" in text or len(text.strip()) < 3:
            return None
        return text.strip()

    def transcribe_audio(
        self,
        audio_path,
        *,
        model: str = "gpt-4o-mini-transcribe",
    ) -> Optional[str]:
        """Transkribiert eine lokal extrahierte Audiospur per OpenAI API."""
        from pathlib import Path as _P

        path = _P(audio_path)
        if not path.is_file() or path.stat().st_size <= 0:
            return None
        try:
            with path.open("rb") as handle:
                r = self._request_with_retry(
                    "POST",
                    "/audio/transcriptions",
                    files={"file": (path.name, handle, "audio/mpeg")},
                    data={
                        "model": (model or "gpt-4o-mini-transcribe").strip(),
                        "response_format": "json",
                    },
                    timeout=max(90, self.timeout),
                )
            r.raise_for_status()
            text = (r.json().get("text") or "").strip()
            return text if len(text) >= 3 else None
        except requests.exceptions.HTTPError as e:
            logger.error(
                "OpenAI Audio HTTP %s: %s",
                e.response.status_code if e.response is not None else "?",
                (e.response.text or "")[:300] if e.response is not None else str(e),
            )
            return None
        except Exception as e:
            logger.error("OpenAI Audio Call: %s", e)
            return None

    def _call_vision(self, b64_data: str, mime: str, prompt: str) -> Optional[str]:
        """Multimodal-Call: prompt + 1 Bild als base64. Kein response_format
        (Vision liefert Freitext, kein JSON). Höherer max_tokens damit lange
        Caption-Bilder vollständig transkribiert werden."""
        try:
            r = self._request_with_retry(
                "POST",
                "/chat/completions",
                json={
                    "model": self.model,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url",
                             "image_url": {"url": f"data:image/{mime};base64,{b64_data}"}},
                        ],
                    }],
                    "temperature": 0.2,
                    "max_tokens": 1500,
                },
                timeout=60,  # Vision ist langsamer als text-only
            )
            r.raise_for_status()
            data = r.json()
            choices = data.get("choices") or []
            if not choices:
                return None
            return (choices[0].get("message") or {}).get("content", "").strip()
        except requests.exceptions.HTTPError as e:
            logger.error(
                "OpenAI Vision HTTP %s: %s",
                e.response.status_code if e.response is not None else "?",
                (e.response.text or "")[:300] if e.response is not None else str(e),
            )
            return None
        except Exception as e:
            logger.error(f"OpenAI Vision Call: {e}")
            return None

    def _extract_pdf_via_vision(self, pdf_path) -> Optional[str]:
        """Render gescanntes PDF (kein Text-Layer) zu PNG und schick die Seiten
        einzeln an Vision. Pure-Python via PyMuPDF — kein poppler-utils nötig.

        Limit: max. 3 Seiten. Rezepte sind selten länger; Multi-Page-Kochbücher
        in einem Folder sind eh ein anderer Use-Case (sollten gesplittet sein).
        Bei 150 DPI ist 1 Seite ~700KB PNG → ~30KB base64-tokens → ~$0.003 pro
        Seite mit gpt-4o-mini Vision.
        """
        try:
            import pymupdf
        except ImportError:
            logger.error(
                "pymupdf nicht installiert — PDF-Vision-Fallback nicht möglich. "
                "Im Container ausführen: pip install pymupdf"
            )
            return None
        import base64
        try:
            doc = pymupdf.open(str(pdf_path))
        except Exception as e:
            logger.warning(f"PyMuPDF kann {pdf_path} nicht öffnen: {e}")
            return None

        max_pages = min(len(doc), 3)
        parts = []
        try:
            for i in range(max_pages):
                page = doc[i]
                pix = page.get_pixmap(dpi=150)
                png_bytes = pix.tobytes(output="png")
                b64 = base64.b64encode(png_bytes).decode()
                prompt = (
                    f"Du siehst Seite {i+1} eines gescannten Rezept-PDFs. "
                    "Lies allen lesbaren Text raus und gib eine zusammenhängende "
                    "deutsche Rezept-Beschreibung mit Zutaten (mit Mengen falls "
                    "lesbar) und Zubereitungs-Schritten zurück. "
                    "Wenn die Seite nichts Rezept-Artiges zeigt, antworte exakt: "
                    "KEINE_REZEPT_DATEN"
                )
                txt = self._call_vision(b64, "png", prompt)
                if txt and "KEINE_REZEPT_DATEN" not in txt:
                    parts.append(txt.strip())
        finally:
            doc.close()

        if not parts:
            return None
        full = "\n\n".join(parts)
        if len(full) < 20:
            return None
        logger.info(
            f"PDF-Vision-Extract: {pdf_path.name} "
            f"({len(full)} chars, {len(parts)}/{max_pages} Seiten)"
        )
        return full

    def audit_recipe_consistency(self, name: str, description: str,
                                  type_name: Optional[str],
                                  category: Optional[str],
                                  folder_name: Optional[str] = None) -> dict:
        """KI-Sanity-Check für ein Rezept: passt Name+Kategorie+Folder zur
        Description?

        Rückgabe (Schema garantiert):
          {
            "category_ok": bool, "category_suggestion": str|None, "category_reason": str|None,
            "name_ok":     bool, "name_suggestion":     str|None, "name_reason":     str|None,
            "folder_ok":   bool, "folder_suggestion":   str|None, "folder_reason":   str|None,
          }

        Ein Single-Call macht ALLE drei Checks — spart ~66% API-Cost.
        folder_name wird nur geprüft wenn der Caller einen mitgibt; bei
        None gibt's keinen folder_mismatch (Result _ok=true).
        """
        system = (
            "Du bist ein Rezept-Klassifikator. Schaue dir Name, Folder-Kategorie, "
            "Folder-Name und Beschreibung eines Rezepts an und entscheide:\n"
            "1. Passt die Kategorie (z.B. 'Hauptgericht/Pasta') zum Inhalt der "
            "Beschreibung? Wenn nicht, schlage eine bessere Kategorie als "
            "'Typ/Kategorie' vor.\n"
            "2. Ist der Name aussagekräftig (kein Datei-Stub wie '00001.mp4', "
            "kein generisches 'recipe_2024')? Wenn nicht, schlage einen "
            "kurzen deutschen Rezept-Namen vor (max 60 Zeichen).\n"
            "3. Ist der FOLDER-Name passend zur Beschreibung? Folder-Namen haben "
            "meist Underscores statt Spaces (z.B. 'Brokkoli_mit_Knoblauch'). "
            "Wenn der Folder-Name den Inhalt grob beschreibt, ist's OK (auch "
            "wenn nicht 1:1 zum 'name'). Falls Stub wie '00001' oder völlig "
            "falsch: schlage folder-tauglichen Namen vor (kurz, _ statt Spaces, "
            "ASCII bevorzugt aber Umlaute erlaubt).\n\n"
            "Antworte ausschliesslich mit JSON nach diesem Schema:\n"
            '{"category_ok":true|false,"category_suggestion":"Typ/Kategorie"|null,'
            '"category_reason":"kurz, max 80 chars"|null,'
            '"name_ok":true|false,"name_suggestion":"Neuer Name"|null,'
            '"name_reason":"kurz, max 80 chars"|null,'
            '"folder_ok":true|false,"folder_suggestion":"Neuer_Folder_Name"|null,'
            '"folder_reason":"kurz, max 80 chars"|null}\n\n'
            "Strenge Regeln:\n"
            "- Bei tatsächlicher Übereinstimmung: _ok=true und Vorschlag/Reason=null.\n"
            "- Bei _ok=false: Vorschlag UND Reason müssen gesetzt sein.\n"
            "- Kategorie-Vorschlag im Format 'Typ/Kategorie'.\n"
            "- Folder-Vorschlag: nur Buchstaben/Ziffern/_- erlaubt, keine /\\:*?\"<>|.\n"
            "- Sei konservativ: nur deutlich abweichende Fälle markieren."
        )
        user_msg = (
            f"Name: {name or '(leer)'}\n"
            f"Aktuelle Kategorie: {type_name or '?'}/{category or '?'}\n"
            f"Folder-Name: {folder_name or '(unbekannt — folder_ok=true zurückgeben)'}\n"
            f"Beschreibung:\n{(description or '')[:3000]}"
        )
        content = self._call(system, user_msg)
        default = {
            "category_ok": True, "category_suggestion": None, "category_reason": None,
            "name_ok": True, "name_suggestion": None, "name_reason": None,
            "folder_ok": True, "folder_suggestion": None, "folder_reason": None,
        }
        if not content:
            return default

        try:
            data = json.loads(content)
            return {
                "category_ok": bool(data.get("category_ok", True)),
                "category_suggestion": (data.get("category_suggestion") or None),
                "category_reason": (data.get("category_reason") or None),
                "name_ok": bool(data.get("name_ok", True)),
                "name_suggestion": (data.get("name_suggestion") or None),
                "name_reason": (data.get("name_reason") or None),
                "folder_ok": bool(data.get("folder_ok", True)),
                "folder_suggestion": (data.get("folder_suggestion") or None),
                "folder_reason": (data.get("folder_reason") or None),
            }
        except Exception as e:
            logger.warning(f"audit_recipe_consistency JSON-Parse: {e} | {content[:200]}")
            return default

    def optimize_shopping_list(self, items: list[dict]) -> list[dict]:
        """Schlägt nur Anzeigenamen und Einkaufsbereiche für Cart-Items vor.

        Mengen und Einheiten werden absichtlich nicht an die KI übertragen und
        später ausschließlich serverseitig aus dem Original übernommen.
        """
        from ..recipes.shopping_optimizer import SHOPPING_CATEGORIES

        candidates = [
            {"id": int(item["id"]), "name": str(item.get("name") or "")[:200]}
            for item in items[:200]
        ]
        if not candidates:
            return []
        categories = ", ".join(SHOPPING_CATEGORIES)
        system = (
            "Du optimierst eine deutsche Einkaufsliste. Vereinheitliche nur "
            "offensichtliche Schreibweisen und Singular/Plural, ohne Produkte "
            "zu erfinden oder genauer zu machen als die Eingabe. Ordne jeden "
            f"Artikel genau einem Bereich zu: {categories}. "
            "Antworte ausschließlich als JSON im Schema "
            '{"items":[{"id":1,"name":"Kartoffeln","category":"Obst & Gemüse"}]}. '
            "Jede Eingabe-ID muss genau einmal vorkommen. IDs und Namen dürfen "
            "nicht vertauscht werden. Mengen sind nicht Teil deiner Aufgabe."
        )
        content = self._call(
            system,
            "Artikel:\n" + json.dumps(candidates, ensure_ascii=False, indent=2),
        )
        if not content:
            return []
        try:
            data = json.loads(content)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        result = data.get("items")
        return result if isinstance(result, list) else []

    def compute_nutrition(self, ingredients: list, servings: Optional[int]) -> Optional[dict]:
        """Schätzt Kalorien + Makros (Protein/Kohlenhydrate/Fett) PRO PORTION
        auf Basis der Zutaten-Liste. Returnt None bei zu wenig Input oder
        wenn die KI 0/leere Werte liefert.

        Single-Call ~$0.0005/Rezept. Schätzungen sind nicht laborgenau —
        Werte werden im UI mit '~' prefixed als Indikation."""
        if not ingredients:
            return None
        # Sinnvoller Default falls servings unbekannt — Werte sind dann
        # 'pro Portion' bei 4 Portionen-Annahme, im UI hingewiesen
        srv = int(servings) if servings and int(servings) >= 1 else 4

        ing_lines = []
        for i, ing in enumerate(ingredients[:60]):
            if not ing.get("name"):
                continue
            parts = []
            amt = ing.get("amount")
            if amt is not None:
                parts.append(f"{amt:g}" if isinstance(amt, float) else str(amt))
            if ing.get("unit"):
                parts.append(ing["unit"])
            parts.append(ing["name"])
            ing_lines.append(f"[{i}] " + " ".join(parts))
        if not ing_lines:
            return None

        system = (
            "Du bist Ernährungs-Experte. Schätze die Nährwerte PRO PORTION für "
            "ein Rezept auf Basis der Zutaten + Portionen-Anzahl.\n\n"
            "Antworte AUSSCHLIESSLICH mit JSON nach diesem Schema:\n"
            '{"calories": int, "protein_g": float, "carbs_g": float, "fat_g": float, '
            '"ingredients": [{"i": int, "kcal": int}]}\n\n'
            "Regeln:\n"
            "- calories: ganze Zahl, kcal PRO Portion (nicht gesamt!)\n"
            "- protein_g / carbs_g / fat_g: Gramm PRO Portion, 1 Nachkommastelle\n"
            "- ingredients: pro Zutat die GESAMT-kcal für die genannte Menge "
            "(NICHT pro Portion), 'i' = die Nummer in [] vor der Zutat\n"
            "- Mengen vor 'pro Portion' durch Portionen-Anzahl teilen\n"
            "- Gewürze/Salz: ignorierbar (calories vernachlässigbar, kcal=0)\n"
            "- Bei Bereichen: Mittel nehmen\n"
            "- Realistische Bandbreiten (Hauptgericht 300-900 kcal, Dessert 200-600 kcal)\n"
            "- Nur Schätzung, nicht laborgenau — Genauigkeit ±15% reicht\n"
            "- Bei zu wenig Info (nur 1-2 Zutaten ohne Mengen): "
            '{"calories":0,"protein_g":0,"carbs_g":0,"fat_g":0,"ingredients":[]}'
        )
        user_msg = f"Portionen: {srv}\n\nZutaten:\n" + "\n".join(ing_lines)
        content = self._call(system, user_msg)
        if not content:
            return None
        try:
            data = json.loads(content)
            cal = int(data.get("calories", 0) or 0)
            if cal <= 0:
                return None
            per_ing = {}
            for x in (data.get("ingredients") or []):
                try:
                    idx = int(x["i"]); kc = int(round(float(x.get("kcal", 0) or 0)))
                    if kc > 0:
                        per_ing[idx] = kc
                except (KeyError, ValueError, TypeError):
                    continue
            return {
                "calories": cal,
                "protein_g": round(float(data.get("protein_g", 0) or 0), 1),
                "carbs_g": round(float(data.get("carbs_g", 0) or 0), 1),
                "fat_g": round(float(data.get("fat_g", 0) or 0), 1),
                "per_ingredient": per_ing,
            }
        except Exception as e:
            logger.warning(f"compute_nutrition JSON: {e} | {content[:200]}")
            return None

    def health(self) -> bool:
        """Pingt GET /v1/models. 401 = Key falsch, 200 = ok.
        Loggt den konkreten Grund bei Fehler - sonst sieht der User nur
        'AI nicht erreichbar' ohne zu wissen ob Key, Netz oder Account-Problem."""
        try:
            r = self.request("GET", "/models", timeout=15)
            if r.status_code == 200:
                return True
            if r.status_code == 401:
                logger.error("OpenAI health: API-Key ungültig (HTTP 401)")
            elif r.status_code == 403:
                logger.error("OpenAI health: Zugriff verweigert (HTTP 403) - Account/Billing prüfen")
            elif r.status_code == 429:
                logger.error("OpenAI health: Rate limit (HTTP 429)")
            else:
                logger.error("OpenAI health: HTTP %s", r.status_code)
            return False
        except requests.exceptions.Timeout:
            logger.error("OpenAI health: Timeout (15s) - Internet vom Container aus?")
            return False
        except requests.exceptions.ConnectionError:
            logger.error("OpenAI health: keine Verbindung")
            return False
        except Exception as e:
            logger.error("OpenAI health: unerwarteter Fehler: %s", type(e).__name__)
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
            "- name: konkrete Zutat auf Deutsch, Singular bevorzugt und ohne Adjektive. "
            "Frische Sortenbezeichnungen wie Cherrytomate, Cocktailtomate oder "
            "Kirschtomate beibehalten.\n"
            "- Tomate, Cherrytomate, Cocktailtomate und Kirschtomate werden später "
            "für die Einkaufsliste gemeinsam als 'tomate' normalisiert. Verarbeitete "
            "Produkte wie passierte Tomaten, Dosentomaten und Tomatenmark bleiben getrennt.\n"
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

    def analyze_recipe_content(self, description: str,
                                existing_tags: Optional[List[str]] = None,
                                existing_canonical: Optional[List[str]] = None) -> dict:
        """Kombinierter Call: extrahiert Zutaten + Zubereitungs-Schritte +
        Portionen-Anzahl + stilistische Tags + Allergiker-Einschätzung in
        EINEM API-Roundtrip.

        Optional kann der Caller die DB-Stammdaten mitgeben:
          existing_tags: bestehende Tag-Namen — KI soll diese bevorzugen
            statt neue, ähnliche Varianten zu erfinden ('pasta' vs 'Pasta').
          existing_canonical: bestehende canonical_name-Werte der Zutaten
            — KI soll Zutaten-Namen so wählen dass das Canonical-Mapping
            in app.recipes.canonicalize.canonical_name() denselben Wert
            ergibt. Verhindert Dubletten wie 'Tomate' / 'Tomaten' / 'tomato'.

        Spart gegenüber separaten Calls ~40% Tokens und ~50% Latenz.

        Die KI liefert für vier Allergengruppen nur eine strukturierte
        Vorprüfung. Positive Frei-von-Tags werden anschließend weiterhin
        deterministisch aus den canonical_names berechnet und benötigen bei
        neuen Analysen zusätzlich das eindeutige KI-Urteil ``frei``.
        """
        # Hint-Sections nur dann anhängen wenn der Caller Werte mitgibt.
        # Cap auf 80 Items damit der Prompt nicht aufbläht — bei mehr
        # nimmt die KI eh den Hint nur als grobe Orientierung.
        hint = ""
        if existing_tags:
            tags_sample = sorted(set(t.strip() for t in existing_tags if t))[:80]
            if tags_sample:
                hint += (
                    "\n\nBESTEHENDE TAGS in der DB (bevorzuge diese exakte Schreibweise "
                    "statt ähnliche Varianten zu erfinden — z.B. 'pasta' statt 'Pasta', "
                    "'meal-prep' statt 'meal_prep'):\n  "
                    + ", ".join(tags_sample)
                )
        if existing_canonical:
            can_sample = sorted(set(c.strip() for c in existing_canonical if c))[:120]
            if can_sample:
                hint += (
                    "\n\nBESTEHENDE ZUTATEN-NAMEN in der DB (wähle den Namen deutsch, "
                    "im Singular und ohne Adjektive. Konkrete frische Sorten wie "
                    "'Cherrytomate' oder 'Cocktailtomate' bleiben im Namen erhalten; "
                    "sie werden später gemeinsam als 'tomate' normalisiert. Verarbeitete "
                    "Produkte wie 'passierte Tomaten', 'Dosentomaten' und 'Tomatenmark' "
                    "bleiben eigenständig. Orientiere dich an dieser Liste):\n  "
                    + ", ".join(can_sample)
                )

        system = (
            "Du analysierst deutschsprachige Rezept-Texte aus verschiedenen Quellen "
            "(TikTok-/Instagram-Captions, Koch-Blogs, PDF-Exports von Rezept-Websites, "
            "Markdown-Notizen, Bullet-Listen) und extrahierst Zutaten, Zubereitungs-"
            "Schritte, Portionen-Anzahl, stilistische Tags und eine vorsichtige "
            "Allergiker-Einschätzung. "
            "Antworte AUSSCHLIESSLICH mit gültigem JSON nach diesem Schema:\n"
            '{"ingredients":[\n'
            '  {"name":"Tomaten","amount":2,"unit":"Stück","raw":"2 große Tomaten"},\n'
            '  ...\n'
            "],\n"
            '"steps":[\n'
            '  {"instruction":"Wasser zum Kochen bringen.","timer_seconds":null},\n'
            '  {"instruction":"Spaghetti 8 Minuten kochen.","timer_seconds":480}\n'
            "],\n"
            '"servings":4,\n'
            '"tags":["italienisch","pasta","schnell","one-pot"],\n'
            '"allergen_info":{\n'
            '  "gluten":"enthält",\n'
            '  "lactose":"frei",\n'
            '  "egg":"unklar",\n'
            '  "nuts":"frei"\n'
            '}\n'
            "}\n\n"
            "═══ KERNREGEL ═══\n"
            "Mengen-Angaben sind das stärkste Signal für Rezept-Inhalt. WENN der Text "
            "explizite Zutaten mit Mengen enthält (z.B. '50 g Walnüsse', '1 Kopf Brokkoli', "
            "'2 Knoblauchzehen', '175 g Butter') — egal in welchem Format (Fließtext, "
            "Markdown-Bullets '- 50 g Butter', Aufzählung mit '•', englische Labels "
            "INGREDIENTS/SERVINGS, PDF-Print-Header drumherum) — MÜSSEN diese Zutaten "
            "extrahiert werden. Der umgebende Text (Werbung, Hashtags, PDF-Header, "
            "Datums-Stempel, Print-Buttons) wird IGNORIERT, die Mengen ZÄHLEN.\n\n"
            "REGELN ZUTATEN:\n"
            "- amount: Zahl oder null. Bei Bereichen ('2-3 Eier', '1-2 Bund') Mittel oder Untergrenze.\n"
            "- unit: nur aus: g, kg, ml, l, TL, EL, Stück, Prise, Bund, Zehe, Scheibe, "
            "Blatt, Pck, Dose, Tasse, Flasche, Glas. Sonst null.\n"
            "- name: konkrete Zutat selbst, deutsche Form, Singular bevorzugt und ohne Adjektive. "
            "Frische Sortenbezeichnungen wie Cherrytomate, Cocktailtomate oder "
            "Kirschtomate beibehalten.\n"
            "- Tomate, Cherrytomate, Cocktailtomate und Kirschtomate werden später "
            "für die Einkaufsliste gemeinsam als 'tomate' normalisiert. Verarbeitete "
            "Produkte wie passierte Tomaten, Dosentomaten und Tomatenmark bleiben getrennt.\n"
            "- raw: genauer Text-Snippet aus der Beschreibung wie es da steht.\n"
            "- Englische Zutaten-Namen ins Deutsche übersetzen (oats → Haferflocken).\n\n"
            "REGELN SCHRITTE:\n"
            "- instruction: vollständiger deutscher Satz, max 200 Zeichen.\n"
            "- timer_seconds: NUR bei konkretem Zeitwert. '8 Min köcheln' → 480. "
            "'kurz anbraten', 'goldbraun', 'über Nacht' → null.\n"
            "- Reihenfolge muss der Zubereitung entsprechen.\n"
            "- Wenn keine expliziten Schritte vorhanden (nur Zutaten-Liste): leeres Array, "
            "  aber Zutaten trotzdem extrahieren!\n\n"
            "REGELN PORTIONEN:\n"
            "- servings: Anzahl Portionen (1-12) wenn explizit ('für 2 Personen', 'SERVINGS 1', "
            "'Rezept für 6 Stück'). Sonst null. Nicht raten.\n\n"
            "REGELN TAGS (3-7 Tags, nur aus dieser festen Liste — keine Erfindungen!):\n"
            "  Küche:    italienisch, asiatisch, mediterran, deutsch, mexikanisch, indisch, "
            "amerikanisch, französisch, orientalisch, thailändisch, japanisch, chinesisch\n"
            "  Kategorie: pasta, pizza, salat, suppe, eintopf, auflauf, bowl, wrap, "
            "burger, sandwich, dessert, kuchen, gebäck, getränk, dip, snack, frühstück\n"
            "  Stil:     schnell, einfach, aufwendig, meal-prep, kinderfreundlich, "
            "low-carb, high-protein, one-pot, kalorienarm, comfort-food, streetfood, "
            "gesund, sommerlich, winterlich, party, fingerfood, grillen, ofen, kalt\n"
            "  KEINE Diät-Tags wie 'vegan' oder 'laktosefrei' — die berechnen wir selbst aus "
            "den Zutaten, weil das sicherer ist.\n\n"
            "REGELN ALLERGIKER-INFO:\n"
            "- Für gluten, lactose, egg und nuts ist exakt einer dieser Werte erlaubt: "
            "frei, enthält, unklar.\n"
            "- enthält: Eine direkte, zusammengesetzte oder typische Quelle ist in den "
            "Zutaten erkennbar (z.B. Sojasauce bei Gluten, Molke bei Laktose, "
            "Mayonnaise bei Ei, Pesto oder Nougat bei Nüssen).\n"
            "- frei: NUR wenn die Zutatenliste ausreichend vollständig ist und weder "
            "eine direkte noch eine versteckte oder mehrdeutige Quelle erkennbar ist.\n"
            "- unklar: Bei unvollständiger Zutatenliste, unbekannten Fertigprodukten, "
            "Mischungen, Brühen, Saucen, möglicher Kreuzkontamination oder jeder anderen "
            "Unsicherheit. Niemals raten; bei Zweifel immer unklar.\n"
            "- Die Angabe ist eine Vorprüfung und keine medizinische Garantie.\n\n"
            "NUR bei wirklich rezept-freiem Text (Begrüßung, reine Werbung, nur Hashtags, "
            "nur Meta-Daten ohne Zutaten): "
            '{"ingredients":[],"steps":[],"servings":null,"tags":[],"allergen_info":'
            '{"gluten":"unklar","lactose":"unklar","egg":"unklar","nuts":"unklar"}}. '
            "Bei vorhandenen Zutaten-Mengen NIEMALS leer zurückgeben."
            + hint
        )
        content = self._call(system, f"Beschreibung:\n\n{description[:6000]}")
        if not content:
            # _call hat None returnt (length-Truncation, network error,
            # leerer choice). Aufrufer muss das als Fehler behandeln und
            # NICHT als 'leer aber erfolgreich'. Zurück None statt empty
            # dict — der Worker markiert dann als 'error' statt 'ok'.
            return None
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
                        if timer <= 0 or timer > 86400:
                            timer = None
                    except (TypeError, ValueError):
                        timer = None
                steps_out.append({"instruction": instr, "timer_seconds": timer})
            # Servings
            servings = data.get("servings")
            if servings is not None:
                try:
                    servings = int(servings)
                    if servings < 1 or servings > 50:
                        servings = None
                except (TypeError, ValueError):
                    servings = None
            # Tags — bei der KI dem festen Vokabular vertrauen, aber defensiv
            # lowercase + dedup + cap auf 8 (sonst halluzinierte Listen mit 20+)
            raw_tags = data.get("tags") or []
            tags_out = []
            seen = set()
            if isinstance(raw_tags, list):
                for t in raw_tags:
                    if not isinstance(t, str):
                        continue
                    norm = t.strip().lower()
                    if not norm or len(norm) > 30 or norm in seen:
                        continue
                    seen.add(norm)
                    tags_out.append(norm)
                    if len(tags_out) >= 8:
                        break
            # Allergiker-Info — unvollständige/ungültige KI-Antworten werden
            # niemals positiv ausgelegt, sondern pro Feld zu "unklar".
            from ..recipes.auto_tags import normalize_allergen_info
            allergen_info = normalize_allergen_info(data.get("allergen_info"))
            return {
                "ingredients": ings_out,
                "steps": steps_out,
                "servings": servings,
                "tags": tags_out,
                "allergen_info": allergen_info,
            }
        except Exception as e:
            logger.warning(f"OpenAI Recipe-Content JSON-Parse: {e} | {content[:200]}")
            return {
                "ingredients": [],
                "steps": [],
                "servings": None,
                "tags": [],
                "allergen_info": None,
            }

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
