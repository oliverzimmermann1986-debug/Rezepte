from typing import get_args

import pytest

from app.recipes.meal_constraints import CATEGORY_NAMES, quota_selection, recipe_categories
from app.routes.api_discovery import MealCategory
from tests.test_discovery import _recipe


@pytest.mark.parametrize('title,category', [
    ('Nudelsalat', 'salad'), ('Griechischer Salat', 'salad'), ('Kartoffelsuppe', 'soup'),
    ('Cremige Kürbis-Suppe', 'soup'), ('Linseneintopf', 'stew'),
    ('Kartoffelauflauf', 'casserole'), ('Lasagne', 'casserole'), ('Gemüsegratin', 'casserole'),
    ('Pizza Margherita', 'pizza'), ('Flammkuchen', 'pizza'), ('Chicken Wrap', 'wrap'),
    ('Burrito', 'wrap'), ('Chicken soup', 'soup'),
])
def test_dish_types_recognize_recipe_titles(title, category):
    assert category in recipe_categories({'name': title}, set(), set())


def test_dish_types_do_not_use_ingredients_or_a_side_dish():
    assert recipe_categories({'name': 'Curry'}, set(), {'salat', 'suppengewürz', 'gemüsebrühe', 'pizzagewürz'}) == []
    assert recipe_categories({'name': 'Wrap mit Salat'}, set(), {'salat'}) == ['wrap']
    assert recipe_categories({'name': 'Hähnchen auf Salat'}, set(), {'salat'}) == []
    assert recipe_categories({'name': 'Suppengewürz'}, set(), set()) == []
    assert recipe_categories({'name': 'Salatöl'}, set(), set()) == []


def test_recipe_metadata_and_ingredient_types_are_combined():
    assert recipe_categories({'name': 'Familiengericht', 'type': 'Suppe'}, set(), {'kartoffel'}) == ['potato', 'soup']
    assert recipe_categories({'name': 'Sommergericht', 'category': 'Salat'}, {'vegan'}, {'nudeln'}) == ['pasta', 'salad']
    assert recipe_categories({'name': 'Rezept'}, {'suppe'}, set()) == ['soup']
    assert set(get_args(MealCategory)) == set(CATEGORY_NAMES)


def test_exact_salad_soup_and_pasta_quotas_allow_overlap(client, test_db):
    pasta_salad = _recipe(test_db, 'Nudelsalat', ('Nudeln', 'Tomaten'), tags=('vegan',))
    salad = _recipe(test_db, 'Gurkensalat', ('Gurke',), tags=('vegan',))
    soup = _recipe(test_db, 'Kartoffelsuppe', ('Kartoffeln',), tags=('vegan',))
    _recipe(test_db, 'Wrap mit Salat', ('Salat',))
    response = client.post('/api/discovery/meal-plan', json={'count': 3, 'category_quotas': [
        {'category': 'salad', 'count': 2}, {'category': 'soup', 'count': 1}, {'category': 'pasta', 'count': 1}]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert {item['recipe_id'] for item in result['items']} == {pasta_salad, salad, soup}
    assert result['constraints_satisfied'] and result['search_complete']
    assert all(item['missing'] == 0 for item in result['missing_quotas'])


def test_salad_day_uses_joint_solver_and_respects_previous_week(client, test_db):
    previous = _recipe(test_db, 'A Alter Salat', ('Gurke',), tags=('vegan',))
    new = _recipe(test_db, 'B Neuer Salat', ('Tomate',), tags=('vegan',))
    soup = _recipe(test_db, 'Suppe', ('Karotte',), tags=('vegan',))
    test_db.meal_plan_add(planned_for='2026-10-05', recipe_id=previous, planned_servings=2)
    response = client.post('/api/discovery/meal-plan', json={'count': 2, 'week_start': '2026-10-12',
        'avoid_recent_days': 7, 'category_quotas': [{'category': 'salad', 'count': 1}, {'category': 'soup', 'count': 1}],
        'weekday_rules': [{'weekday': 1, 'category': 'salad'}, {'weekday': 5, 'category': 'soup'}]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert [(row['weekday'], row['recipe_id']) for row in result['items']] == [(1, new), (5, soup)]
    assert result['constraints_satisfied']


def test_zero_soup_quota_excludes_soup_without_excluding_stock_in_other_food(client, test_db):
    _recipe(test_db, 'Tomatensuppe', ('Tomaten',))
    curry = _recipe(test_db, 'Gemüsecurry', ('Gemüsebrühe', 'Karotten'))
    response = client.post('/api/discovery/meal-plan', json={'count': 1, 'category_quotas': [{'category': 'soup', 'count': 0}]})
    assert response.status_code == 200
    assert [row['recipe_id'] for row in response.json()['items']] == [curry]


def test_more_than_four_categories_can_be_submitted_and_missing_dishes_stay_missing(client, test_db):
    _recipe(test_db, 'Reisgericht', ('Reis',))
    response = client.post('/api/discovery/meal-plan', json={'count': 7,
        'category_quotas': [{'category': category, 'count': 1} for category in CATEGORY_NAMES]})
    assert response.status_code == 200, response.text
    assert not response.json()['constraints_satisfied']
    assert len(response.json()['missing_quotas']) == 10


@pytest.mark.parametrize('quotas', [[{'category': 'salad', 'count': 1}] * 2,
                                  [{'category': 'unknown', 'count': 1}],
                                  [{'category': 'soup', 'count': 8}]])
def test_new_category_validation_remains_strict(client, quotas):
    assert client.post('/api/discovery/meal-plan', json={'count': 7, 'category_quotas': quotas}).status_code == 422


def test_many_categories_keep_a_hard_state_budget_without_relaxing_quotas():
    candidates = [{'recipe_id': index, 'name': name, 'categories': [name], 'vegetarian': False,
                   '_coverage': 0, '_category': name} for index, name in enumerate(CATEGORY_NAMES, 1)]
    quotas = [{'category': name, 'count': 1} for name in CATEGORY_NAMES]
    selected, complete = quota_selection(candidates, 7, 0, quotas, state_limit=3)
    assert not complete
    assert len({item['recipe_id'] for item in selected}) == len(selected)
    assert all(sum(quota['category'] in item['categories'] for item in selected) <= quota['count'] for quota in quotas)
