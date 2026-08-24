"""SQL-Bausteine für Rezeptfilter und Suchrelevanz.

Getrennt von ``Database`` gehalten, damit die große Persistenzklasse nicht
zusätzlich Such-/Filter-Fachlogik enthält. Die Funktionen erzeugen nur SQL und
Parameter; ausgeführt wird weiterhin zentral über ``Database.conn``.
"""
from __future__ import annotations

from typing import Any, List, Optional, Protocol, Tuple


class _SearchDatabase(Protocol):
    def _append_smart_search(self, where: List[str], params: List[Any], search: str) -> None: ...
    def search_synonyms_map(self) -> dict[str, list[str]]: ...


def build_recipe_filters(
    db: _SearchDatabase,
    *,
    type: Optional[str] = None,
    category: Optional[str] = None,
    categories: Optional[List[str]] = None,
    folder_prefix: Optional[str] = None,
    tag_ids: Optional[List[int]] = None,
    ingredient_canonical: Optional[List[str]] = None,
    ingredient_excluded: Optional[List[str]] = None,
    search: Optional[str] = None,
    ingredients_status: Optional[str] = None,
    verified: Optional[bool] = None,
    favorite_only: bool = False,
    min_rating: int = 0,
    ratings: Optional[List[int]] = None,
    needs_manual_care: Optional[bool] = None,
    include_deleted: bool = False,
    only_deleted: bool = False,
) -> Tuple[str, List[Any]]:
    params: List[Any] = []
    where: List[str] = []
    if type:
        where.append("r.type = ?")
        params.append(type)
    category_values = list(dict.fromkeys(
        value.strip()
        for value in ([category] if category else []) + list(categories or [])
        if value and value.strip()
    ))
    if category_values:
        placeholders = ",".join("?" for _ in category_values)
        where.append(f"r.category IN ({placeholders})")
        params.extend(category_values)
    if folder_prefix:
        where.append("r.folder_path LIKE ?")
        params.append(folder_prefix + "%")
    if search:
        db._append_smart_search(where, params, search)
    if tag_ids:
        for tag_id in tag_ids:
            where.append(
                "EXISTS (SELECT 1 FROM recipe_tags rt "
                "WHERE rt.recipe_id=r.id AND rt.tag_id=?)"
            )
            params.append(tag_id)
    if ingredient_canonical:
        for ingredient in ingredient_canonical:
            where.append(
                "EXISTS (SELECT 1 FROM recipe_ingredients ri "
                "WHERE ri.recipe_id=r.id AND ri.canonical_name=?)"
            )
            params.append(ingredient)
    if ingredient_excluded:
        for ingredient in ingredient_excluded:
            where.append(
                "NOT EXISTS (SELECT 1 FROM recipe_ingredients ri "
                "WHERE ri.recipe_id=r.id AND ri.canonical_name=?)"
            )
            params.append(ingredient)
    if ingredients_status:
        where.append("r.ingredients_status = ?")
        params.append(ingredients_status)
    if verified is not None:
        where.append("COALESCE(r.user_verified, 0) = ?")
        params.append(1 if verified else 0)
    if favorite_only:
        where.append("r.is_favorite = 1")
    rating_values = sorted(set(int(value) for value in (ratings or [])))
    if any(value < 0 or value > 5 for value in rating_values):
        raise ValueError("Bewertungen müssen zwischen 0 und 5 liegen")
    if rating_values:
        placeholders = ",".join("?" for _ in rating_values)
        where.append(f"COALESCE(r.rating, 0) IN ({placeholders})")
        params.extend(rating_values)
    elif min_rating > 0:
        where.append("r.rating >= ?")
        params.append(min_rating)
    if needs_manual_care is not None:
        # "Manuell pflegen" = keine Zutaten ODER keine Schritte. Dieselbe
        # Bedingung, die die Listen-Antwort als needs_manual_care ausgibt —
        # hier als SQL, damit Filter und `total` nicht auseinanderlaufen
        # (vorher filterte die iOS-App erst nach dem LIMIT und zählte damit
        # nur innerhalb der geladenen Seite).
        empty_ingredients = (
            "NOT EXISTS (SELECT 1 FROM recipe_ingredients mri WHERE mri.recipe_id=r.id)"
        )
        empty_steps = (
            "NOT EXISTS (SELECT 1 FROM recipe_steps mrs WHERE mrs.recipe_id=r.id)"
        )
        if needs_manual_care:
            where.append(f"({empty_ingredients} OR {empty_steps})")
        else:
            where.append(f"(NOT {empty_ingredients} AND NOT {empty_steps})")
    if only_deleted:
        where.append("r.deleted_at IS NOT NULL")
    elif not include_deleted:
        where.append("r.deleted_at IS NULL")
    return (" WHERE " + " AND ".join(where) if where else ""), params


def search_rank_sql(db: _SearchDatabase, search: str) -> Tuple[str, List[Any]]:
    """Bewertet Treffer vor LIMIT/OFFSET direkt in SQLite."""
    from .search import parse_search_query

    plan = parse_search_query(search, db.search_synonyms_map())
    group_scores: List[str] = []
    params: List[Any] = []
    for group in plan.positive_groups:
        term_scores: List[str] = []
        for raw_term in group[:8]:
            term = str(raw_term or "").strip()
            if len(term) < 2:
                continue
            contains = f"%{term}%"
            term_scores.append(
                "CASE "
                "WHEN COALESCE(r.name,'') COLLATE NOCASE = ? THEN 100 "
                "WHEN COALESCE(r.name,'') LIKE ? COLLATE NOCASE THEN 75 "
                "WHEN COALESCE(r.name,'') LIKE ? COLLATE NOCASE THEN 55 "
                "WHEN EXISTS (SELECT 1 FROM recipe_ingredients sri "
                "  WHERE sri.recipe_id=r.id AND "
                "  (COALESCE(sri.canonical_name,'') LIKE ? COLLATE NOCASE "
                "   OR COALESCE(sri.name,'') LIKE ? COLLATE NOCASE)) THEN 38 "
                "WHEN (COALESCE(r.type,'') LIKE ? COLLATE NOCASE "
                "   OR COALESCE(r.category,'') LIKE ? COLLATE NOCASE) THEN 24 "
                "WHEN COALESCE(r.description,'') LIKE ? COLLATE NOCASE THEN 12 "
                "ELSE 0 END"
            )
            params.extend([
                term, f"{term}%", contains, contains,
                contains, contains, contains, contains,
            ])
        if term_scores:
            group_scores.append(
                term_scores[0] if len(term_scores) == 1
                else "MAX(" + ", ".join(term_scores) + ")"
            )
    if not group_scores:
        return "0", []
    freshness = "MIN(2.0, COALESCE(r.source_added_at, r.indexed_at, 0) / 10000000000.0)"
    return "(" + " + ".join(group_scores) + f" + {freshness})", params
