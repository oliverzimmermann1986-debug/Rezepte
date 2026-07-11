from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.analyzer import RecipeAnalysis, WeddingAnalysis
from app.security import SameOriginMiddleware


def test_ai_output_is_normalized_and_confidence_clamped():
    recipe = RecipeAnalysis.from_dict({
        "rezeptname": {"unexpected": "object"},
        "typ": "  Hauptgericht\n  ",
        "kategorie": ["Pasta"],
        "confidence": "nan",
    })
    assert recipe.name == "Unbekannt"
    assert recipe.type == "Hauptgericht"
    assert recipe.category is None
    assert recipe.confidence == 0.0

    wedding = WeddingAnalysis.from_dict({
        "name": "  Gelbe   Schleifen  ",
        "kategorie": "Deko",
        "confidence": 99,
    })
    assert wedding.name == "Gelbe Schleifen"
    assert wedding.category == "Deko"
    assert wedding.confidence == 1.0


def test_same_origin_normalizes_default_ports_and_rejects_scheme_change():
    app = FastAPI()
    app.add_middleware(SameOriginMiddleware)

    @app.post("/write")
    def write():
        return {"ok": True}

    with TestClient(app, base_url="https://example.test") as client:
        assert client.post(
            "/write",
            headers={"Origin": "https://example.test:443"},
        ).status_code == 200
        assert client.post(
            "/write",
            headers={"Origin": "http://example.test"},
        ).status_code == 403
        assert client.post(
            "/write",
            headers={"Origin": "https://evil.example"},
        ).status_code == 403
