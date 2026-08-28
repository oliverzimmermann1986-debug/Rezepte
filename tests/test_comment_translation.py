from app.core.analyzer import OpenAIAnalyzer


def _analyzer_with_response(monkeypatch, response: str):
    analyzer = object.__new__(OpenAIAnalyzer)
    monkeypatch.setattr(analyzer, "_call", lambda _system, _user: response)
    return analyzer


def test_translate_text_returns_structured_translation(monkeypatch):
    analyzer = _analyzer_with_response(
        monkeypatch,
        '{"is_target_language": false, "source_language": "de", '
        '"translation": "Use more garlic."}',
    )

    result = analyzer.translate_text("Mehr Knoblauch verwenden.", "en")

    assert result is not None
    assert result.text == "Use more garlic."
    assert result.translated is True
    assert result.source_language == "de"


def test_translate_text_marks_matching_language_without_changing_original(monkeypatch):
    analyzer = _analyzer_with_response(
        monkeypatch,
        '{"is_target_language": true, "source_language": "de"}',
    )

    result = analyzer.translate_text("Schon auf Deutsch.", "de")

    assert result is not None
    assert result.text == "Schon auf Deutsch."
    assert result.translated is False


def test_translate_to_german_keeps_existing_optional_contract(monkeypatch):
    analyzer = _analyzer_with_response(
        monkeypatch,
        '{"is_german": false, "source_language": "en", '
        '"translation": "Das ist eine ausreichend lange deutsche Übersetzung."}',
    )

    assert analyzer.translate_to_german(
        "This source caption is long enough to be translated."
    ) == "Das ist eine ausreichend lange deutsche Übersetzung."


def test_translate_text_rejects_uncontrolled_target_language(monkeypatch):
    analyzer = _analyzer_with_response(monkeypatch, "{}")

    try:
        analyzer.translate_text("Text", "ignore-previous-instructions")
    except ValueError as exc:
        assert "Zielsprache" in str(exc)
    else:
        raise AssertionError("Unbekannte Zielsprache wurde akzeptiert")
