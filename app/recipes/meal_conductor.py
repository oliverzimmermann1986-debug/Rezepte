"""Deterministischer Mehr-Rezept-Zeitplan fuer einen gemeinsamen Serviertermin."""
from __future__ import annotations

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
        return max(1, (seconds + 59) // 60), False
    # Unzeitgesteuerte Arbeitsschritte bleiben sichtbar und werden konservativ
    # mit fuenf Minuten eingeplant. Die UI kennzeichnet diese Schaetzung klar.
    return 5, True


def _saturated_intervals(
    reservations: Sequence[tuple[int, int]],
    capacity: int,
) -> List[tuple[int, int]]:
    """Returnt Intervalle, in denen keine weitere Belegung frei ist."""
    deltas: Dict[int, int] = {}
    for occupied_start, occupied_end in reservations:
        if occupied_end <= occupied_start:
            continue
        deltas[occupied_start] = deltas.get(occupied_start, 0) + 1
        deltas[occupied_end] = deltas.get(occupied_end, 0) - 1

    concurrent = 0
    previous: int | None = None
    blocked: List[tuple[int, int]] = []
    for moment in sorted(deltas):
        if previous is not None and previous < moment and concurrent >= capacity:
            if blocked and blocked[-1][1] == previous:
                blocked[-1] = (blocked[-1][0], moment)
            else:
                blocked.append((previous, moment))
        concurrent += deltas[moment]
        previous = moment
    return blocked


def _latest_available_slot(
    end: int,
    duration: int,
    reservations: Sequence[tuple[int, int]],
    capacity: int,
) -> tuple[int, int, int]:
    """Findet den spaetesten freien Slot durch Spruenge an Belegungsgrenzen."""
    desired_end = end
    blocked = _saturated_intervals(reservations, capacity)
    while True:
        start = end - duration
        conflicts = [
            interval
            for interval in blocked
            if interval[0] < end and interval[1] > start
        ]
        if not conflicts:
            return start, end, desired_end - end
        # Rueckwaerts muss der Kandidat vor dem fruehesten aktuell
        # geschnittenen Sperrintervall enden. Dadurch ist die Suche an die
        # Zahl der Reservierungsgrenzen statt an die Minutenzahl gebunden.
        end = min(blocked_start for blocked_start, _blocked_end in conflicts)


def _moment(day: date, minute: int) -> datetime:
    try:
        return datetime.combine(day, time.min) + timedelta(minutes=minute)
    except OverflowError as exc:
        raise ValueError("Der Zeitplan liegt ausserhalb des unterstuetzten Datumsbereichs") from exc


def build_conductor_plan(
    entries: Iterable[Mapping[str, Any]],
    steps_by_recipe: Mapping[int, Sequence[Mapping[str, Any]]],
    *,
    planned_for: date,
    serve_hour: int,
    serve_minute: int,
    burners: int,
    oven_slots: int,
    active_cooks: int = 1,
) -> Dict[str, Any]:
    """Plant Rezeptketten rueckwaerts und entzerrt knappe Kuechen-Ressourcen.

    Manuelle ``counter``-Schritte belegen je eine aktiv kochende Person fuer
    ihre gesamte Dauer. Herd- und Ofenschritte belegen dagegen das jeweilige
    Geraet; ihre Timerphase gilt als passive Garzeit.
    """
    selected = list(entries)
    if not selected:
        raise ValueError("Fuer diesen Tag sind keine Gerichte geplant")

    capacity_values = (
        ("Aktive Koech:innen", active_cooks, 8),
        ("Herdplatten", burners, 8),
        ("Ofenplaetze", oven_slots, 4),
    )
    for label, value, maximum in capacity_values:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"{label} muessen zwischen 1 und {maximum} liegen")
    if (
        isinstance(serve_hour, bool)
        or not isinstance(serve_hour, int)
        or not 0 <= serve_hour <= 23
        or isinstance(serve_minute, bool)
        or not isinstance(serve_minute, int)
        or not 0 <= serve_minute <= 59
    ):
        raise ValueError("Servierzeit muss eine gueltige Uhrzeit sein")

    capacities = {
        "counter": active_cooks,
        "burner": burners,
        "oven": oven_slots,
    }
    reservations: Dict[str, List[tuple[int, int]]] = {
        resource: [] for resource in capacities
    }
    serve_total = serve_hour * 60 + serve_minute
    events: List[Dict[str, Any]] = []
    estimated_count = 0
    adjusted_count = 0
    adjustments_by_resource = {resource: 0 for resource in capacities}

    def recipe_minutes(entry: Mapping[str, Any]) -> int:
        steps = steps_by_recipe.get(int(entry["recipe_id"]), ())
        return sum(_duration_minutes(step)[0] for step in steps)

    # Lange Ketten zuerst einplanen; das haelt kurze Beilagen nahe am Serviertermin.
    selected.sort(
        key=lambda item: (
            -recipe_minutes(item),
            str(item.get("recipe_name") or "").casefold(),
            int(item["recipe_id"]),
        )
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
            start, end, shifted = _latest_available_slot(
                end,
                duration,
                reservations[resource],
                capacities[resource],
            )
            reservations[resource].append((start, end))
            cursor = start
            estimated_count += int(estimated)
            adjusted_count += int(shifted > 0)
            adjustments_by_resource[resource] += int(shifted > 0)
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

    events.sort(
        key=lambda item: (
            item["start_at"],
            str(item["recipe_name"]).casefold(),
            item["recipe_id"],
            item["step_number"],
        )
    )
    start_at = min(item["start_at"] for item in events)
    start_moment = datetime.fromisoformat(start_at)
    serve_at = _moment(planned_for, serve_total)
    warnings: List[str] = []
    if estimated_count:
        warnings.append(
            f"{estimated_count} Schritt(e) ohne Zeitangabe wurden mit je 5 Minuten geschaetzt."
        )
    if adjustments_by_resource["counter"]:
        warnings.append(
            f"{adjustments_by_resource['counter']} manuelle(r) Schritt(e) wurden wegen "
            "begrenzter Kochkapazitaet vorgezogen."
        )
    device_adjustments = (
        adjustments_by_resource["burner"] + adjustments_by_resource["oven"]
    )
    if device_adjustments:
        warnings.append(
            f"{device_adjustments} Schritt(e) wurden wegen Ofen- oder Herdbelegung vorgezogen."
        )
    if start_moment.date() < planned_for:
        days_before = (planned_for - start_moment.date()).days
        warnings.append(
            f"Der Ablauf beginnt {days_before} Tag(e) vor dem Serviertag."
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
            "counter_adjustments": adjustments_by_resource["counter"],
            "device_adjustments": device_adjustments,
            "active_cooks": active_cooks,
            "burners": burners,
            "oven_slots": oven_slots,
            "duration_minutes": int((serve_at - start_moment).total_seconds() // 60),
            "starts_previous_day": start_moment.date() < planned_for,
        },
    }
