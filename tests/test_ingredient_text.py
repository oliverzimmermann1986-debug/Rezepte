import json

import pytest

from app.core.analyzer import OpenAIAnalyzer
from app.recipes.ingredient_text import clean_ingredient_text, is_non_ingredient
from app.recipes.pdf_recipe_extract import parse_ingredient_lines, prepare_recipe_ingredients


NOISE = [
    '#chicken', '#chickenwrap', '#fajita', '#rezepte', '#foodies',
    'Chicken Fajita Wrap Rezept – einfache Zutaten & zwei Soßen',
    'Lead', 'Zutaten (übersichtlich)', 'Soße 1 (Skyr-Gurken-Soße)',
    'Soße 2 (Mayo-Ketchup-Soße)', 'Außerdem',
]
FOOD = [
    'Hähnchenbrust', 'Paprika', 'Zwiebeln', 'Mais',
    'Salz, Pfeffer, Paprika edelsüß, Hähnchengewürz, Oregano, Knoblauchpulver',
    'Ölspray', 'Skyr', 'geraspelte Gurke', 'Zitrone', 'Salz, Pfeffer',
    'Knoblauchzehe', 'Petersilie', 'Mayo', 'Ketchup', 'Paprika edelsüß',
    'Wraps oder arabisches Brot', 'Salat', 'geriebener Käse',
]


def test_wrap_caption_rejects_metadata_and_keeps_all_real_ingredients():
    source = 'Zutaten:\n' + '\n'.join('- ' + name for name in FOOD) + '\n' + '\n'.join(NOISE)
    assert [item['name'] for item in parse_ingredient_lines(source)] == FOOD


@pytest.mark.parametrize('text', NOISE + ['#vegan #pasta', '＃foodies'])
def test_metadata_is_not_an_ingredient(text):
    # Full-width hashtags can occur in captions too.
    assert is_non_ingredient(text)


@pytest.mark.parametrize('name', FOOD + ['Soße', 'Sriracha-Sauce', 'Dip', 'Teig', 'Mayo-Ketchup-Soße'])
def test_real_food_is_preserved(name):
    assert not is_non_ingredient(name)


def test_markdown_sections_and_trailing_hashtags_keep_amounts():
    items = parse_ingredient_lines('## Zutaten (übersichtlich)\n- 250 g Mehl #backen\n'
                                   '### Für den Teig\n- 2 Eier\n## Zubereitung\nTeig verrühren.')
    assert [(item['name'], item['amount']) for item in items] == [('Mehl', 250), ('Eier', 2)]
    assert clean_ingredient_text('#chicken') == ''


def test_ai_cannot_turn_a_hashtag_into_a_food_evidence():
    items = prepare_recipe_ingredients([
        {'name': 'Hähnchen', 'raw': '#chicken'},
        {'name': 'Salz #foodies', 'raw': 'Salz #foodies'},
        *[{'name': name} for name in NOISE],
    ])
    assert [item['name'] for item in items] == ['Salz']


@pytest.mark.parametrize('method', ['analyze_ingredients', 'analyze_recipe_content'])
def test_analyzer_rejects_metadata_even_if_model_returns_it(monkeypatch, method):
    analyzer = OpenAIAnalyzer(api_key='test-only')
    monkeypatch.setattr(analyzer, '_call', lambda *_: json.dumps({
        'ingredients': [{'name': name, 'raw': name} for name in FOOD + NOISE]
                       + [{'name': 'Hähnchen', 'raw': '#chicken'}],
    }))
    result = getattr(analyzer, method)('Zutaten:\n' + '\n'.join(FOOD + NOISE))
    items = result if isinstance(result, list) else result['ingredients']
    assert [item['name'] for item in items] == FOOD


@pytest.mark.parametrize('method', ['recipe_set_extraction_result', 'recipe_apply_extraction_result'])
def test_extraction_persistence_rejects_noise_before_shopping_catalog(test_db, tmp_path, method):
    rid = test_db.recipe_upsert(url='https://example.com/wrap', name='Wrap', type='Hauptgericht',
                               category='Test', folder_path=str(tmp_path), description=None,
                               thumb_filename=None, video_filename=None, source_added_at=1)
    ingredients = [{'name': name, 'canonical_name': name.casefold()} for name in FOOD + NOISE]
    if method == 'recipe_set_extraction_result':
        test_db.recipe_set_extraction_result(rid, 'ok', ingredients)
    else:
        test_db.recipe_apply_extraction_result(rid, ingredients=ingredients, steps=[], servings=2, auto_tags=[])
    assert [item['name'] for item in test_db.recipe_ingredients_get(rid)] == FOOD
    with test_db.conn() as connection:
        assert not connection.execute("SELECT 1 FROM shopping_products WHERE display_name LIKE '#%'").fetchone()
