"""Deterministischer Mehr-Rezept-Zeitplan fuer einen gemeinsamen Serviertermin."""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Sequence


_OVEN_MARKERS = (
    "ofen", "backofen", "backen", "ueberbacken", "überbacken", "gratinieren",
)
_BURNER_MARKERS = (
    "herd", "pfanne", "topf", "kochen", "koecheln", "köcheln", "braten",
    "anbraten", "erhitzen", "aufkochen", "schmelzen",
)


def _resource_for(instruction: str) -> str:
    text = " ".join(str(instruction or "").casefold().split())
    if any(marker in text for marker in _OVEN_MARKERS):
        return "oven"
    if any(marker in text for marker in _BURNER_MARKERS):
        return "burner"
    return "counter"


def _duration_minutes(step: Mapping[str, Any]) -> tuple[int, bool]:
    raw = step.get("timer_seconds")
    try:
        seconds = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        seconds = 0
    if seconds > 0:
        return max(1, math.ceil(seconds / 60)), False
    # Unzeitgesteuerte Arbeitsschritte bleiben sichtbar und werden konservativ
    # mit fuenf Minuten eingeplant. Die UI kennzeichnet diese Schaetzung klar.
    return 5, True


def _slot_is_available(
    start: int,
    end: int,
    reservations: Sequence[tuple[int, int]],
    capacity: int,
) -> bool:
    if capacity < 1:
        return False
    for minute in range(start, end):
        concurrent = sum(
            1 for occupied_start, occupied_end in reservations
            if occupied_start < minute + 1 and occupied_end > minute
        )
        if concurrent >= capacity:
            return False
    return True


def _moment(day: date, minute: int) -> datetime:
    return datetime.combine(day, time.min) + timedelta(minutes=minute)


def build_conductor_plan(
    entries: Iterable[Mapping[str, Any]],
    steps_by_recipe: Mapping[int, Sequence[Mapping[str, Any]]],
    *,
    planned_for: date,
    serve_hour: int,
    serve_minute: int,
    burners: int,
    oven_slots: int,
) -> Dict[str, Any]:
    """Plant Rezeptketten rueckwaerts und entzerrt knappe Geraete-Ressourcen."""
    selected = list(entries)
    if not selected:
        raise ValueError("Fuer diesen Tag sind keine Gerichte geplant")

    capacities = {"burner": burners, "oven": oven_slots}
    reservations: Dict[str, List[tuple[int, int]]] = {"burner": [], "oven": []}
    serve_total = serve_hour * 60 + serve_minute
    events: List[Dict[str, Any]] = []
    estimated_count = 0
    adjusted_count = 0

    def recipe_minutes(entry: Mapping[str, Any]) -> int:
        steps = steps_by_recipe.get(int(entry["recipe_id"]), ())
        return sum(_duration_minutes(step)[0] for step in steps)

    # Lange Ketten zuerst einplanen; das haelt kurze Beilagen nahe am Serviertermin.
    selected.sort(
        key=lambda item: (-recipe_minutes(item), str(item.get("recipe_name") or ""))
    )
    for entry in selected:
        recipe_id = int(entry["recipe_id"])
        steps = list(steps_by_recipe.get(recipe_id, ()))
        if not steps:
            raise ValueError(
                f"{entry.get('recipe_name') or 'Ein Rezept'} hat keine Zubereitungsschritte"
            )
        cursor = serve_total
        recipe_events: List[Dict[str, Any]] = []
        for fallback_number, step in reversed(list(enumerate(steps, start=1))):
            duration, estimated = _duration_minutes(step)
            resource = _resource_for(str(step.get("instruction") or ""))
            end = cursor
            start = end - duration
            shifted = 0
            if resource in capacities:
                while not _slot_is_available(
                    start,
                    end,
                    reservations[resource],
                    capacities[resource],
                ):
                    start -= 1
                    end -= 1
                    shifted += 1
                    if shifted > 24 * 60:
                        raise ValueError("Der Zeitplan kann mit den gewaehlten Geraeten nicht erstellt werden")
                reservations[resource].append((start, end))
            cursor = start
            estimated_count += int(estimated)
            adjusted_count += int(shifted > 0)
            start_moment = _moment(planned_for, start)
            end_moment = _moment(planned_for, end)
            step_number = int(step.get("step_number") or fallback_number)
            recipe_events.append({
                "id": f"{recipe_id}-{step_number}",
                "recipe_id": recipe_id,
                "recipe_name": entry.get("recipe_name") or f"Rezept #{recipe_id}",
                "planned_servings": entry.get("planned_servings"),
                "step_number": step_number,
                "instruction": str(step.get("instruction") or "").strip(),
                "resource": resource,
                "duration_minutes": duration,
                "estimated": estimated,
                "resource_adjusted": shifted > 0,
                "start_at": start_moment.isoformat(timespec="minutes"),
                "end_at": end_moment.isoformat(timespec="minutes"),
                "start_time": start_moment.strftime("%H:%M"),
                "end_time": end_moment.strftime("%H:%M"),
            })
        events.extend(reversed(recipe_events))

    events.sort(key=lambda item: (item["start_at"], item["recipe_name"], item["step_number"]))
    start_at = min(item["start_at"] for item in events)
    serve_at = _moment(planned_for, serve_total)
    warnings: List[str] = []
    if estimated_count:
        warnings.append(
            f"{estimated_count} Schritt(e) ohne Zeitangabe wurden mit je 5 Minuten geschaetzt."
        )
    if adjusted_count:
        warnings.append(
            f"{adjusted_count} Schritt(e) wurden wegen Ofen- oder Herdbelegung vorgezogen."
        )
    return {
        "planned_for": planned_for.isoformat(),
        "serve_at": serve_at.isoformat(timespec="minutes"),
        "serve_time": serve_at.strftime("%H:%M"),
        "start_at": start_at,
        "events": events,
        "warnings": warnings,
        "summary": {
            "recipes": len(selected),
            "steps": len(events),
            "estimated_steps": estimated_count,
            "resource_adjustments": adjusted_count,
            "burners": burners,
            "oven_slots": oven_slots,
        },
    }
