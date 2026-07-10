"""
VMAN Training Planner 1.86 — Assistent-tidssimulator.

Dette modul gør den tidligere optimizer-placeholder brugbar i Assistenten.
Det er bevidst en lille, deterministisk søgning ovenpå den eksisterende
simulator, ikke en påstand om global optimum.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import random
import time

from .config import POSITION_STATS, POSITION_WEIGHTS
from .models import TrainingInstruction, TrainingCycle
from .rules import exercises_for_position, normalize_position
from .simulator import days_between_ages, simulate_program, expand_program


GENETIC_ALGORITHM_METHOD = "Genetic Algorithm"
GENETIC_ALGORITHM_ALIASES = {"Genetic Algorithm", "Genetisk algoritme"}
PENALTY_TOLERANCE_LIMITS = {
    "Ingen": 1.15,
    "Lav": 1.20,
    "Mellem": 1.40,
    "Høj": None,
    "None": 1.15,
    "Low": 1.20,
    "Medium": 1.40,
    "High": None,
}


def _canonical_search_method(search_method: str) -> str:
    if search_method in GENETIC_ALGORITHM_ALIASES:
        return GENETIC_ALGORITHM_METHOD
    return str(search_method or "Beam Search")


def _canonical_penalty_tolerance(value: str) -> str:
    value = str(value or "Høj")
    return value if value in PENALTY_TOLERANCE_LIMITS else "Høj"


@dataclass
class AssistantPlanResult:
    rank: int
    score: float
    rating: float
    uw_rating: Optional[float]
    average: float
    days: int
    method: str
    notes: str
    program: List[TrainingInstruction]
    final_stats: Dict[str, float]


def _clean_start_stats(position: str, start_stats: Dict[str, float]) -> Dict[str, int]:
    position = normalize_position(position)
    cleaned = {}
    for stat in POSITION_STATS[position]:
        try:
            value = int(round(float(start_stats.get(stat, 0))))
        except Exception:
            value = 0
        cleaned[stat] = max(0, min(100, value))
    return cleaned


def _normalise_weights(position: str, weights: Optional[Dict[str, float]]) -> Dict[str, float]:
    position = normalize_position(position)
    source = weights or POSITION_WEIGHTS[position]
    cleaned = {}
    for stat in POSITION_STATS[position]:
        try:
            cleaned[stat] = max(0.0, float(source.get(stat, 0.0)))
        except Exception:
            cleaned[stat] = 0.0
    total = sum(cleaned.values())
    if total <= 0:
        return {stat: 100.0 / len(cleaned) for stat in cleaned}
    return {stat: value * 100.0 / total for stat, value in cleaned.items()}


def _weighted_rating(final_stats: Dict[str, float], weights: Dict[str, float]) -> float:
    total = sum(weights.values()) or 1.0
    return sum(final_stats.get(stat, 0.0) * weight for stat, weight in weights.items()) / total


def _weighted_average(final_stats: Dict[str, float], position: str) -> float:
    stats = POSITION_STATS[normalize_position(position)]
    return sum(final_stats.get(stat, 0.0) for stat in stats) / len(stats)


def _high_weighted_group(weights: Dict[str, float], min_size: int = 2) -> List[str]:
    """Find de stats, som brugeren typisk vil forvente følger hinanden.

    Reglen er bevidst simpel: stats på mindst 40% af største vægt tæller som
    højtvægtede. Det rammer fx de fire keeper-kernestats, angrebets fire
    vigtigste stats og forsvars Tackling/Hurtighed/Acceleration.
    """
    cleaned = {stat: max(0.0, float(weight)) for stat, weight in weights.items()}
    if not cleaned:
        return []
    max_weight = max(cleaned.values())
    if max_weight <= 0:
        return []
    group = [stat for stat, weight in cleaned.items() if weight >= max_weight * 0.40 and weight > 0]
    if len(group) < min_size:
        group = [stat for stat, _weight in sorted(cleaned.items(), key=lambda item: item[1], reverse=True)[:min_size]]
    return group


def _grouped_high_weight_penalty(final_stats: Dict[str, float], weights: Dict[str, float], enabled: bool) -> Tuple[float, str]:
    if not enabled:
        return 0.0, ""
    group = _high_weighted_group(weights)
    if len(group) < 2:
        return 0.0, ""

    values = [float(final_stats.get(stat, 0.0)) for stat in group]
    spread = max(values) - min(values)
    mean = sum(values) / len(values)
    mean_abs_deviation = sum(abs(value - mean) for value in values) / len(values)

    # Tolerance: små forskelle er ok. Større spredning skal koste tydeligt,
    # ellers vinder programmer med én stærkt forsømt kernestat stadig.
    tolerance = 8.0
    penalty = max(0.0, spread - tolerance) * 0.55 + mean_abs_deviation * 0.10
    note = f"Gruppe {min(values):.1f}-{max(values):.1f}"
    return penalty, note


def _apply_intensity_schedule(
    position: str,
    program: List[TrainingInstruction],
    weights: Dict[str, float],
    allow_boost: bool,
    boost_uses: int,
    boost_period: int,
) -> List[TrainingInstruction]:
    """Tving intensitet til den valgte 1/7, 2/7 osv. rytme.

    Dette er vigtigt efter mutation og ved "søg videre", hvor et seed-program
    ellers kan komme til at bære Ekstrem på hele lange faser.
    """
    position = normalize_position(position)
    daily: List[Tuple[str, int, Optional[Dict[str, int]]]] = []
    day = 1
    for phase in program or []:
        for _ in range(max(1, int(phase.days))):
            exercise = str(phase.exercise)
            points = 24 if _is_boost_day(day, allow_boost, boost_uses, boost_period) else 23
            daily.append((exercise, points, _distribution_for_exercise(position, exercise, weights, points)))
            day += 1
    return _compress_daily_sequence(daily)


def _distribution_for_exercise(position: str, exercise: str, weights: Dict[str, float], total_points: int = 23) -> Optional[Dict[str, int]]:
    position = normalize_position(position)
    if exercise == "Træningskamp":
        return None

    allowed = list(exercises_for_position(position)[exercise])
    total_points = max(20, min(24, int(total_points)))
    distribution = {stat: 2 for stat in allowed}
    remaining = total_points - sum(distribution.values())

    ordered = sorted(allowed, key=lambda stat: (weights.get(stat, 0.0), stat), reverse=True)
    guard = 0
    while remaining > 0 and ordered and guard < 1000:
        progressed = False
        for stat in ordered:
            if remaining <= 0:
                break
            if distribution[stat] < 10:
                distribution[stat] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
        guard += 1

    return distribution


def _is_boost_day(day: int, allow: bool, uses: int, period: int) -> bool:
    if not allow:
        return False
    period = max(1, int(period or 1))
    uses = max(1, min(period, int(uses or 1)))
    return ((day - 1) % period) < uses


def _daily_tuple(position: str, exercise: str, weights: Dict[str, float], day: int, allow_boost: bool, boost_uses: int, boost_period: int):
    points = 24 if _is_boost_day(day, allow_boost, boost_uses, boost_period) else 23
    return (exercise, points, _distribution_for_exercise(position, exercise, weights, points))


def _compress_daily_sequence(daily: List[Tuple[str, int, Optional[Dict[str, int]]]]) -> List[TrainingInstruction]:
    if not daily:
        return []

    program: List[TrainingInstruction] = []
    last_exercise, last_points, last_distribution = daily[0]
    count = 1

    for exercise, points, distribution in daily[1:]:
        if exercise == last_exercise and points == last_points and distribution == last_distribution:
            count += 1
        else:
            program.append(TrainingInstruction(count, last_exercise, last_distribution, last_points))
            last_exercise, last_points, last_distribution = exercise, points, distribution
            count = 1

    program.append(TrainingInstruction(count, last_exercise, last_distribution, last_points))
    return program


def _exercise_scores(position: str, weights: Dict[str, float]) -> Dict[str, float]:
    position = normalize_position(position)
    scores = {}
    for exercise, stats in exercises_for_position(position).items():
        # Gennemsnit pr. påvirket stat, så brede øvelser ikke automatisk vinder alt.
        scores[exercise] = sum(weights.get(stat, 0.0) for stat in stats) / max(1, len(stats))
    return scores


def _build_candidate_programs(
    position: str,
    days: int,
    weights: Dict[str, float],
    change_every_days: int,
    search_method: str,
    search_time: str,
    allow_boost: bool,
    boost_uses: int,
    boost_period: int,
) -> List[Tuple[str, List[TrainingInstruction]]]:
    position = normalize_position(position)
    search_method = _canonical_search_method(search_method)
    days = max(1, int(days))
    change_every_days = max(1, int(change_every_days or 1))
    exercises = list(exercises_for_position(position).keys())
    scores = _exercise_scores(position, weights)
    ranked = sorted(exercises, key=lambda ex: scores.get(ex, 0.0), reverse=True)

    candidates: List[Tuple[str, List[TrainingInstruction]]] = []

    # 1) Rene baseline-programmer.
    for exercise in exercises:
        daily = [_daily_tuple(position, exercise, weights, day, allow_boost, boost_uses, boost_period) for day in range(1, days + 1)]
        candidates.append((f"Kun {exercise}", _compress_daily_sequence(daily)))

    # 2) Skift mellem topøvelser.
    for width in range(2, min(6, len(ranked)) + 1):
        daily = []
        block_index = 0
        for day in range(1, days + 1):
            if (day - 1) % change_every_days == 0 and day > 1:
                block_index += 1
            exercise = ranked[block_index % width]
            daily.append(_daily_tuple(position, exercise, weights, day, allow_boost, boost_uses, boost_period))
        candidates.append((f"Top {width}, skift hver {change_every_days}d", _compress_daily_sequence(daily)))

    # 3) Indsæt Træningskamp med forskellige intervaller.
    if "Træningskamp" in exercises:
        non_match = [ex for ex in ranked if ex != "Træningskamp"] or ranked
        for every in (3, 5, 7, 10, 14):
            daily = []
            block_index = 0
            for day in range(1, days + 1):
                if day % every == 0:
                    exercise = "Træningskamp"
                else:
                    if (day - 1) % change_every_days == 0 and day > 1:
                        block_index += 1
                    exercise = non_match[block_index % min(3, len(non_match))]
                daily.append(_daily_tuple(position, exercise, weights, day, allow_boost, boost_uses, boost_period))
            candidates.append((f"Topøvelser + Træningskamp hver {every}d", _compress_daily_sequence(daily)))

    # 4) Deterministisk random/mutation, styret af metode og søgetid.
    time_depth = {
        "10 sek": 12,
        "1 min": 32,
        "5 min": 70,
        "15 min": 110,
        "1 time": 160,
        "4 timer": 200,
        "8 timer": 220,
    }.get(search_time, 70)

    method_factor = 1.0
    if search_method == "Monte Carlo":
        method_factor = 1.8
    elif search_method == "Genetic Algorithm":
        method_factor = 1.5
    elif search_method == "Simulated Annealing":
        method_factor = 1.3
    elif search_method == "Hybrid: Beam + Mutation":
        method_factor = 1.7

    rng = random.Random(162)
    choice_weights = [max(0.1, scores.get(ex, 0.0)) for ex in exercises]
    exploration_count = int(time_depth * method_factor)

    for n in range(exploration_count):
        daily = []
        current = rng.choices(exercises, weights=choice_weights, k=1)[0]
        for day in range(1, days + 1):
            should_switch = day == 1 or ((day - 1) % change_every_days == 0)
            mutation_rate = 0.03
            if search_method in {"Monte Carlo", "Hybrid: Beam + Mutation"}:
                mutation_rate = 0.08
            elif search_method == "Simulated Annealing":
                mutation_rate = max(0.02, 0.12 * (1.0 - day / max(1, days)))

            if should_switch or rng.random() < mutation_rate:
                if search_method == "Monte Carlo":
                    current = rng.choice(exercises)
                else:
                    current = rng.choices(exercises, weights=choice_weights, k=1)[0]

            daily.append(_daily_tuple(position, current, weights, day, allow_boost, boost_uses, boost_period))

        candidates.append((f"{search_method} kandidat {n + 1}", _compress_daily_sequence(daily)))

    # Deduplicate.
    seen = set()
    unique: List[Tuple[str, List[TrainingInstruction]]] = []
    for label, program in candidates:
        signature = tuple(
            (phase.days, phase.exercise, phase.training_points, tuple(sorted((phase.distribution or {}).items())))
            for phase in program
        )
        if signature not in seen:
            seen.add(signature)
            unique.append((label, program))

    return unique


def _program_note(program: List[TrainingInstruction], max_parts: int = 4) -> str:
    parts = []
    for phase in program[:max_parts]:
        if isinstance(phase, TrainingCycle):
            parts.append(f"{phase.repetitions}× {phase.name} ({phase.base_days}d)")
        else:
            intensity = "Ekstrem" if phase.training_points == 24 else "Hård"
            parts.append(f"{phase.days}d {phase.exercise} ({intensity})")
    if len(program) > max_parts:
        parts.append("…")
    return " → ".join(parts)


def assistant_search_training_plans(
    position: str,
    start_stats: Dict[str, float],
    start_age: float,
    end_age: float,
    xp: float,
    weights: Optional[Dict[str, float]] = None,
    change_every_days: int = 3,
    search_method: str = "Beam Search",
    search_time: str = "5 min",
    xp_variance: bool = False,
    reference_training_points: int = 23,
    experience_bonus_enabled: bool = False,
    experience_bonus_ratio: float = 1.10,
    experience_bonus_knee: str = "linear",
    allow_intensity_boost: bool = False,
    intensity_boost_uses: int = 1,
    intensity_boost_period_days: int = 7,
    group_high_weighted_stats: bool = False,
    penalty_tolerance: str = "Høj",
    top_n: int = 10,
) -> List[AssistantPlanResult]:
    """Returnér Top N-programmer til Assistentens side 3/3."""
    position = normalize_position(position)
    search_method = _canonical_search_method(search_method)
    penalty_tolerance = _canonical_penalty_tolerance(penalty_tolerance)
    start_age = float(start_age)
    end_age = float(end_age)
    if end_age <= start_age:
        end_age = start_age + 0.1
    days = max(1, days_between_ages(start_age, end_age))

    start_stats_int = _clean_start_stats(position, start_stats)
    weights_norm = _normalise_weights(position, weights)

    candidates = _build_candidate_programs(
        position=position,
        days=days,
        weights=weights_norm,
        change_every_days=change_every_days,
        search_method=search_method,
        search_time=search_time,
        allow_boost=allow_intensity_boost,
        boost_uses=intensity_boost_uses,
        boost_period=intensity_boost_period_days,
    )

    results: List[AssistantPlanResult] = []
    reference_training_points = max(20, min(24, int(reference_training_points or 23)))

    for label, program in candidates:
        results.append(
            _evaluate_assistant_program(
                label=label,
                program=program,
                position=position,
                start_stats_int=start_stats_int,
                start_age=start_age,
                days=days,
                xp=xp,
                weights_norm=weights_norm,
                xp_variance=xp_variance,
                reference_training_points=reference_training_points,
                experience_bonus_enabled=experience_bonus_enabled,
                experience_bonus_ratio=experience_bonus_ratio,
                experience_bonus_knee=experience_bonus_knee,
                group_high_weighted_stats=group_high_weighted_stats,
                penalty_tolerance=penalty_tolerance,
            )
        )

    results.sort(key=lambda item: (item.score, item.average), reverse=True)
    results = results[:top_n]
    for index, item in enumerate(results, start=1):
        item.rank = index
    return results


SEARCH_TIME_SECONDS = {
    "10 sek": 10,
    "1 min": 60,
    "5 min": 5 * 60,
    "15 min": 15 * 60,
    "1 time": 60 * 60,
    "4 timer": 4 * 60 * 60,
    "8 timer": 8 * 60 * 60,
    # Brute Force bruger manuel søgning: i praksis kører den indtil Stop
    # eller indtil det endelige søgerum er tømt.
    "Manuel": 10 * 365 * 24 * 60 * 60,
    "10 sec": 10,
    "1 hour": 60 * 60,
    "4 hours": 4 * 60 * 60,
    "8 hours": 8 * 60 * 60,
    "Manual": 10 * 365 * 24 * 60 * 60,
}


def search_time_to_seconds(search_time: str) -> int:
    return int(SEARCH_TIME_SECONDS.get(search_time, 5 * 60))


def _insert_top_result(results: List[AssistantPlanResult], result: AssistantPlanResult, top_n: int) -> List[AssistantPlanResult]:
    results.append(result)
    results.sort(key=lambda item: (item.score, item.average), reverse=True)
    del results[top_n:]
    for index, item in enumerate(results, start=1):
        item.rank = index
    return results



def _max_average_ratio_from_stats(stats: Dict[str, float]) -> float:
    values = [float(value) for value in stats.values()]
    if not values:
        return 1.0
    avg = sum(values) / len(values)
    if avg <= 0:
        return 1.0
    return max(values) / avg


def _max_average_ratio_from_history(history) -> float:
    max_ratio = 1.0
    for day in history or []:
        max_ratio = max(max_ratio, _max_average_ratio_from_stats(day.before_stats))
        max_ratio = max(max_ratio, _max_average_ratio_from_stats(day.after_stats))
    return max_ratio


def _penalty_tolerance_penalty(max_ratio: float, tolerance: str) -> Tuple[float, str]:
    tolerance = _canonical_penalty_tolerance(tolerance)
    limit = PENALTY_TOLERANCE_LIMITS[tolerance]
    if limit is None:
        return 0.0, ""
    percent = (max_ratio - 1.0) * 100.0
    limit_percent = (limit - 1.0) * 100.0
    if max_ratio <= limit + 1e-9:
        return 0.0, f"Straftolerance {tolerance}: max {percent:.1f}%"

    # Hård nok til at et resultat over den valgte grænse normalt taber til
    # et lidt lavere VT-resultat, men ikke så hård at tabellen bliver tom.
    excess_points = (max_ratio - limit) * 100.0
    return excess_points * 4.0, f"Straftolerance {tolerance}: max {percent:.1f}% > {limit_percent:.0f}%"


def _evaluate_assistant_program(
    label: str,
    program: List[TrainingInstruction],
    position: str,
    start_stats_int: Dict[str, int],
    start_age: float,
    days: int,
    xp: float,
    weights_norm: Dict[str, float],
    xp_variance: bool,
    reference_training_points: int,
    experience_bonus_enabled: bool,
    experience_bonus_ratio: float,
    experience_bonus_knee: str,
    group_high_weighted_stats: bool = False,
    penalty_tolerance: str = "Høj",
) -> AssistantPlanResult:
    penalty_tolerance = _canonical_penalty_tolerance(penalty_tolerance)
    keep_history = penalty_tolerance != "Høj"
    final_state, history = simulate_program(
        start_stats=start_stats_int,
        program=program,
        position=position,
        xp=float(xp),
        start_age=start_age,
        keep_history=keep_history,
        reference_training_points=reference_training_points,
        experience_bonus_enabled=experience_bonus_enabled,
        experience_bonus_ratio=experience_bonus_ratio,
        experience_bonus_knee=experience_bonus_knee,
        experience_bonus_start_age=start_age,
    )
    final_stats = final_state.stat_values(mode="fractional")
    official_weights = _normalise_weights(position, POSITION_WEIGHTS[position])
    rating = _weighted_rating(final_stats, official_weights)
    uw_rating = _weighted_rating(final_stats, weights_norm)
    average = _weighted_average(final_stats, position)
    note = _program_note(program)
    group_penalty, group_note = _grouped_high_weight_penalty(final_stats, weights_norm, group_high_weighted_stats)
    max_ratio = _max_average_ratio_from_history(history) if keep_history else _max_average_ratio_from_stats(final_stats)
    tolerance_penalty, tolerance_note = _penalty_tolerance_penalty(max_ratio, penalty_tolerance)
    score = uw_rating - group_penalty - tolerance_penalty
    if group_note:
        note += f" | {group_note}"
    if tolerance_note:
        note += f" | {tolerance_note}"

    if xp_variance:
        test_values = [max(1.0, float(xp) - 5.0), float(xp), float(xp) + 5.0]
        ratings = []
        for xp_test in test_values:
            state_test, _ = simulate_program(
                start_stats=start_stats_int,
                program=program,
                position=position,
                xp=xp_test,
                start_age=start_age,
                keep_history=False,
                reference_training_points=reference_training_points,
                experience_bonus_enabled=experience_bonus_enabled,
                experience_bonus_ratio=experience_bonus_ratio,
                experience_bonus_knee=experience_bonus_knee,
                experience_bonus_start_age=start_age,
            )
            ratings.append(_weighted_rating(state_test.stat_values(mode="fractional"), weights_norm))
        note += f" | XP±5 VT {min(ratings):.2f}-{max(ratings):.2f}"

    return AssistantPlanResult(
        rank=0,
        score=score,
        rating=rating,
        uw_rating=uw_rating,
        average=average,
        days=days,
        method=label,
        notes=note,
        program=program,
        final_stats=final_stats,
    )


def _seed_candidate_programs(
    position: str,
    days: int,
    weights: Dict[str, float],
    change_every_days: int,
    allow_boost: bool,
    boost_uses: int,
    boost_period: int,
) -> List[Tuple[str, List[TrainingInstruction]]]:
    exercises = list(exercises_for_position(position).keys())
    scores = _exercise_scores(position, weights)
    ranked = sorted(exercises, key=lambda ex: scores.get(ex, 0.0), reverse=True)
    candidates: List[Tuple[str, List[TrainingInstruction]]] = []

    for exercise in exercises:
        daily = [_daily_tuple(position, exercise, weights, day, allow_boost, boost_uses, boost_period) for day in range(1, days + 1)]
        candidates.append((f"Kun {exercise}", _compress_daily_sequence(daily)))

    for width in range(2, min(6, len(ranked)) + 1):
        daily = []
        block_index = 0
        for day in range(1, days + 1):
            if (day - 1) % change_every_days == 0 and day > 1:
                block_index += 1
            exercise = ranked[block_index % width]
            daily.append(_daily_tuple(position, exercise, weights, day, allow_boost, boost_uses, boost_period))
        candidates.append((f"Top {width}, skift hver {change_every_days}d", _compress_daily_sequence(daily)))

    if "Træningskamp" in exercises:
        non_match = [ex for ex in ranked if ex != "Træningskamp"] or ranked
        for every in (3, 5, 7, 10, 14):
            daily = []
            block_index = 0
            for day in range(1, days + 1):
                if day % every == 0:
                    exercise = "Træningskamp"
                else:
                    if (day - 1) % change_every_days == 0 and day > 1:
                        block_index += 1
                    exercise = non_match[block_index % min(3, len(non_match))]
                daily.append(_daily_tuple(position, exercise, weights, day, allow_boost, boost_uses, boost_period))
            candidates.append((f"Topøvelser + Træningskamp hver {every}d", _compress_daily_sequence(daily)))

    return candidates


def _brute_force_candidate_program(
    position: str,
    days: int,
    weights: Dict[str, float],
    change_every_days: int,
    allow_boost: bool,
    boost_uses: int,
    boost_period: int,
    index: int,
) -> Optional[Tuple[str, List[TrainingInstruction]]]:
    """Deterministisk udtømmende søgning over øvelsesvalg pr. blok.

    Søgerummet er: én øvelse pr. change_every_days-blok. Fordelingen udregnes
    stadig efter den normale stat-vægtning. Ved meget lange perioder bliver
    søgerummet enormt, så metoden er normalt noget man stopper manuelt.
    """
    position = normalize_position(position)
    days = max(1, int(days))
    change_every_days = max(1, int(change_every_days or 1))
    exercises = list(exercises_for_position(position).keys())
    if not exercises:
        return None

    # Vægtede øvelser først, så de mest realistiske kombinationer evalueres tidligt.
    scores = _exercise_scores(position, weights)
    exercises = sorted(exercises, key=lambda ex: scores.get(ex, 0.0), reverse=True)

    block_count = max(1, (days + change_every_days - 1) // change_every_days)
    base = len(exercises)
    max_index = base ** block_count
    if int(index) >= max_index:
        return None

    value = int(index)
    block_choices = []
    for _ in range(block_count):
        block_choices.append(exercises[value % base])
        value //= base

    daily = []
    for day in range(1, days + 1):
        block_index = min((day - 1) // change_every_days, block_count - 1)
        exercise = block_choices[block_index]
        daily.append(_daily_tuple(position, exercise, weights, day, allow_boost, boost_uses, boost_period))

    return (f"Brute Force #{int(index) + 1}", _compress_daily_sequence(daily))


def _random_candidate_program(
    position: str,
    days: int,
    weights: Dict[str, float],
    change_every_days: int,
    search_method: str,
    allow_boost: bool,
    boost_uses: int,
    boost_period: int,
    rng: random.Random,
    index: int,
) -> Tuple[str, List[TrainingInstruction]]:
    search_method = _canonical_search_method(search_method)
    exercises = list(exercises_for_position(position).keys())
    scores = _exercise_scores(position, weights)
    choice_weights = [max(0.1, scores.get(ex, 0.0)) for ex in exercises]

    mutation_rate = 0.03
    if search_method in {"Monte Carlo", "Hybrid: Beam + Mutation"}:
        mutation_rate = 0.10
    elif search_method == "Simulated Annealing":
        mutation_rate = 0.12
    elif search_method == "Genetic Algorithm":
        mutation_rate = 0.06

    daily = []
    current = rng.choices(exercises, weights=choice_weights, k=1)[0]
    local_change_every = max(1, change_every_days + rng.choice([-1, 0, 0, 1]))

    for day in range(1, days + 1):
        should_switch = day == 1 or ((day - 1) % local_change_every == 0)
        day_mutation = mutation_rate
        if search_method == "Simulated Annealing":
            day_mutation = max(0.015, mutation_rate * (1.0 - day / max(1, days)))

        if should_switch or rng.random() < day_mutation:
            if search_method == "Monte Carlo" or rng.random() < 0.18:
                current = rng.choice(exercises)
            else:
                current = rng.choices(exercises, weights=choice_weights, k=1)[0]

        daily.append(_daily_tuple(position, current, weights, day, allow_boost, boost_uses, boost_period))

    return (f"{search_method} #{index}", _compress_daily_sequence(daily))


def _program_signature(program: List[TrainingInstruction]):
    signature = []
    for phase in program:
        if isinstance(phase, TrainingCycle):
            signature.append((
                "cycle",
                phase.name,
                int(phase.repetitions),
                tuple(
                    (sub.days, sub.exercise, sub.training_points, tuple(sorted((sub.distribution or {}).items())))
                    for sub in phase.phases
                ),
            ))
        else:
            signature.append((phase.days, phase.exercise, phase.training_points, tuple(sorted((phase.distribution or {}).items()))))
    return tuple(signature)


def assistant_search_training_plans_timed(
    position: str,
    start_stats: Dict[str, float],
    start_age: float,
    end_age: float,
    xp: float,
    weights: Optional[Dict[str, float]] = None,
    change_every_days: int = 3,
    search_method: str = "Beam Search",
    search_time: str = "5 min",
    xp_variance: bool = False,
    reference_training_points: int = 23,
    experience_bonus_enabled: bool = False,
    experience_bonus_ratio: float = 1.10,
    experience_bonus_knee: str = "linear",
    allow_intensity_boost: bool = False,
    intensity_boost_uses: int = 1,
    intensity_boost_period_days: int = 7,
    group_high_weighted_stats: bool = False,
    penalty_tolerance: str = "Høj",
    top_n: int = 10,
    progress_callback=None,
    stop_event=None,
    time_limit_seconds: Optional[float] = None,
) -> List[AssistantPlanResult]:
    """Tidsstyret Assistent-søgning.

    Funktionen bliver ved med at generere og evaluere kandidater, indtil den
    valgte søgetid er udløbet eller stop_event sættes. Det gør søgetiderne i
    side 3 reelle i stedet for blot at være faste kandidat-counts.
    """
    position = normalize_position(position)
    search_method = _canonical_search_method(search_method)
    penalty_tolerance = _canonical_penalty_tolerance(penalty_tolerance)
    start_age = float(start_age)
    end_age = float(end_age)
    if end_age <= start_age:
        end_age = start_age + 0.1
    days = max(1, days_between_ages(start_age, end_age))

    start_stats_int = _clean_start_stats(position, start_stats)
    weights_norm = _normalise_weights(position, weights)
    reference_training_points = max(20, min(24, int(reference_training_points or 23)))
    change_every_days = max(1, int(change_every_days or 1))
    boost_period = max(1, int(intensity_boost_period_days or 1))
    boost_uses = max(1, min(boost_period, int(intensity_boost_uses or 1)))

    duration = float(time_limit_seconds if time_limit_seconds is not None else search_time_to_seconds(search_time))
    duration = max(0.05, duration)
    started = time.monotonic()
    deadline = started + duration
    next_progress = started

    results: List[AssistantPlanResult] = []
    seen = set()
    evaluated = 0
    rng = random.Random(started + duration + hash((position, search_method, search_time)))

    seed_candidates = _seed_candidate_programs(
        position=position,
        days=days,
        weights=weights_norm,
        change_every_days=change_every_days,
        allow_boost=allow_intensity_boost,
        boost_uses=boost_uses,
        boost_period=boost_period,
    )
    seed_index = 0
    random_index = 1

    def stopped() -> bool:
        return bool(stop_event is not None and stop_event.is_set())

    def publish(force: bool = False):
        nonlocal next_progress
        if progress_callback is None:
            return
        now = time.monotonic()
        if not force and now < next_progress:
            return
        next_progress = now + 1.00
        remaining = max(0.0, deadline - now)
        progress_callback({
            "elapsed": max(0.0, now - started),
            "remaining": remaining,
            "evaluated": evaluated,
            "best_rating": (results[0].uw_rating if getattr(results[0], "uw_rating", None) is not None else results[0].rating) if results else None,
            "results": list(results[:top_n]),
        })

    # Kør mindst én kandidat, også ved meget kort test-timeout.
    while (time.monotonic() < deadline or evaluated == 0) and not stopped():
        if seed_index < len(seed_candidates):
            label, program = seed_candidates[seed_index]
            seed_index += 1
        else:
            label, program = _random_candidate_program(
                position=position,
                days=days,
                weights=weights_norm,
                change_every_days=change_every_days,
                search_method=search_method,
                allow_boost=allow_intensity_boost,
                boost_uses=boost_uses,
                boost_period=boost_period,
                rng=rng,
                index=random_index,
            )
            random_index += 1

        signature = _program_signature(program)
        if signature in seen:
            # Deduplicering må ikke stoppe tidsloopen; prøv bare igen.
            publish()
            continue
        seen.add(signature)

        result = _evaluate_assistant_program(
            label=label,
            program=program,
            position=position,
            start_stats_int=start_stats_int,
            start_age=start_age,
            days=days,
            xp=xp,
            weights_norm=weights_norm,
            xp_variance=xp_variance,
            reference_training_points=reference_training_points,
            experience_bonus_enabled=experience_bonus_enabled,
            experience_bonus_ratio=experience_bonus_ratio,
            experience_bonus_knee=experience_bonus_knee,
            group_high_weighted_stats=group_high_weighted_stats,
            penalty_tolerance=penalty_tolerance,
        )
        evaluated += 1
        _insert_top_result(results, result, top_n)
        publish()

    publish(force=True)
    return results

# ---------- 1.64 search extensions ----------

def _clone_program(program: List[TrainingInstruction]) -> List[TrainingInstruction]:
    cloned = []
    for phase in (program or []):
        if isinstance(phase, TrainingCycle):
            cloned.append(
                TrainingCycle(
                    name=str(phase.name),
                    phases=_clone_program(phase.phases),
                    repetitions=int(phase.repetitions),
                )
            )
        else:
            cloned.append(
                TrainingInstruction(
                    days=int(phase.days),
                    exercise=str(phase.exercise),
                    distribution=None if phase.distribution is None else dict(phase.distribution),
                    training_points=int(getattr(phase, "training_points", 23)),
                )
            )
    return cloned


def _mutate_seed_program(
    seed_program: List[TrainingInstruction],
    position: str,
    weights: Dict[str, float],
    search_method: str,
    allow_boost: bool,
    boost_uses: int,
    boost_period: int,
    rng: random.Random,
    index: int,
) -> Tuple[str, List[TrainingInstruction]]:
    """Lav en lille mutation af et eksisterende program uden at ændre samlet dagtal."""
    position = normalize_position(position)
    search_method = _canonical_search_method(search_method)
    program = _clone_program(seed_program)
    if not program:
        return _random_candidate_program(
            position, 1, weights, 1, search_method,
            allow_boost, boost_uses, boost_period, rng, index,
        )

    exercises = list(exercises_for_position(position).keys())
    scores = _exercise_scores(position, weights)
    choice_weights = [max(0.1, scores.get(ex, 0.0)) for ex in exercises]

    mutation_count = 1
    if search_method in {"Genetic Algorithm", "Hybrid: Beam + Mutation"}:
        mutation_count = 2
    elif search_method == "Monte Carlo":
        mutation_count = 3
    elif search_method == "Simulated Annealing":
        mutation_count = 1 if rng.random() < 0.65 else 2

    for _ in range(mutation_count):
        idx = rng.randrange(len(program))
        phase = program[idx]
        exercise = phase.exercise
        points = int(getattr(phase, "training_points", 23))

        roll = rng.random()
        if roll < 0.70:
            if search_method == "Monte Carlo":
                exercise = rng.choice(exercises)
            else:
                exercise = rng.choices(exercises, weights=choice_weights, k=1)[0]
        elif roll < 0.86:
            points = 24 if points == 23 else 23
        else:
            # Bevar totaldage, men flyt én dag mellem to nabofaser hvis muligt.
            if len(program) > 1:
                j = (idx + rng.choice([-1, 1])) % len(program)
                if program[idx].days > 1:
                    program[idx] = TrainingInstruction(
                        days=program[idx].days - 1,
                        exercise=program[idx].exercise,
                        distribution=program[idx].distribution,
                        training_points=program[idx].training_points,
                    )
                    other = program[j]
                    program[j] = TrainingInstruction(
                        days=other.days + 1,
                        exercise=other.exercise,
                        distribution=other.distribution,
                        training_points=other.training_points,
                    )
                continue

        distribution = _distribution_for_exercise(position, exercise, weights, points)
        program[idx] = TrainingInstruction(
            days=max(1, int(phase.days)),
            exercise=exercise,
            distribution=distribution,
            training_points=max(20, min(24, points)),
        )

    normalized = _apply_intensity_schedule(position, program, weights, allow_boost, boost_uses, boost_period)
    return (f"{search_method} videre #{index}", normalized)


QUEEN_SEEKER_METHODS = [
    "Beam Search",
    "Genetic Algorithm",
    "Simulated Annealing",
    "Monte Carlo",
    "Hybrid: Beam + Mutation",
]

TM_QUEEN_SEEKER_METHOD = "TM17 + Queen Seeker"
TM_QUEEN_CUTOFF_AGES = (17.0, 18.0, 19.0)


def _tm_prefix_days(start_age: float, cutoff_age: float, total_days: int) -> int:
    return max(0, min(int(total_days), int(round((float(cutoff_age) - float(start_age)) * 30))))


def _program_to_daily(program: List[TrainingInstruction]) -> List[Tuple[str, int, Optional[Dict[str, int]]]]:
    daily = []
    for exercise, distribution, points in expand_program(program or []):
        daily.append((exercise, int(points), None if distribution is None else dict(distribution)))
    return daily


def _tm_force_prefix_program(
    position: str,
    source_program: List[TrainingInstruction],
    total_days: int,
    prefix_days: int,
    weights: Dict[str, float],
    allow_boost: bool,
    boost_uses: int,
    boost_period: int,
) -> List[TrainingInstruction]:
    """Bevar Træningskamp-prefix og brug resten af kildeprogrammet som suffix."""
    position = normalize_position(position)
    total_days = max(1, int(total_days))
    prefix_days = max(0, min(total_days, int(prefix_days)))
    source_daily = _program_to_daily(source_program)
    suffix_daily = source_daily[prefix_days:] if len(source_daily) >= total_days else source_daily

    fallback_scores = _exercise_scores(position, weights)
    fallback_exercise = max(fallback_scores, key=fallback_scores.get) if fallback_scores else "Træningskamp"

    daily: List[Tuple[str, int, Optional[Dict[str, int]]]] = []
    for day in range(1, total_days + 1):
        if day <= prefix_days:
            exercise = "Træningskamp"
        else:
            idx = day - prefix_days - 1
            exercise = suffix_daily[idx][0] if idx < len(suffix_daily) else fallback_exercise
        daily.append(_daily_tuple(position, exercise, weights, day, allow_boost, boost_uses, boost_period))
    return _compress_daily_sequence(daily)


def _tm_wrap_suffix_program(
    position: str,
    suffix_program: List[TrainingInstruction],
    total_days: int,
    prefix_days: int,
    weights: Dict[str, float],
    allow_boost: bool,
    boost_uses: int,
    boost_period: int,
) -> List[TrainingInstruction]:
    position = normalize_position(position)
    total_days = max(1, int(total_days))
    prefix_days = max(0, min(total_days, int(prefix_days)))
    suffix_daily = _program_to_daily(suffix_program)

    fallback_scores = _exercise_scores(position, weights)
    fallback_exercise = max(fallback_scores, key=fallback_scores.get) if fallback_scores else "Træningskamp"

    daily: List[Tuple[str, int, Optional[Dict[str, int]]]] = []
    for day in range(1, total_days + 1):
        if day <= prefix_days:
            exercise = "Træningskamp"
        else:
            idx = day - prefix_days - 1
            exercise = suffix_daily[idx][0] if idx < len(suffix_daily) else fallback_exercise
        daily.append(_daily_tuple(position, exercise, weights, day, allow_boost, boost_uses, boost_period))
    return _compress_daily_sequence(daily)


def _tm_seed_candidate_programs(
    position: str,
    total_days: int,
    start_age: float,
    weights: Dict[str, float],
    change_every_days: int,
    allow_boost: bool,
    boost_uses: int,
    boost_period: int,
) -> List[Tuple[str, List[TrainingInstruction]]]:
    candidates: List[Tuple[str, List[TrainingInstruction]]] = []
    for cutoff in TM_QUEEN_CUTOFF_AGES:
        prefix_days = _tm_prefix_days(start_age, cutoff, total_days)
        suffix_days = max(0, total_days - prefix_days)
        if suffix_days <= 0:
            program = _tm_wrap_suffix_program(position, [], total_days, prefix_days, weights, allow_boost, boost_uses, boost_period)
            candidates.append((f"TM til {int(cutoff)} år", program))
            continue
        suffix_seeds = _seed_candidate_programs(
            position=position,
            days=suffix_days,
            weights=weights,
            change_every_days=change_every_days,
            allow_boost=allow_boost,
            boost_uses=boost_uses,
            boost_period=boost_period,
        )
        for label, suffix in suffix_seeds:
            program = _tm_wrap_suffix_program(position, suffix, total_days, prefix_days, weights, allow_boost, boost_uses, boost_period)
            candidates.append((f"TM til {int(cutoff)} år + {label}", program))
    return candidates



def _assistant_constraint_prefix_days(
    start_age: float,
    total_days: int,
    training_match_prelude_enabled: bool,
    training_match_until_age: Optional[float],
) -> int:
    if not training_match_prelude_enabled or training_match_until_age is None:
        return 0
    return max(0, min(int(total_days), int(round((float(training_match_until_age) - float(start_age)) * 30))))


def _exercise_from_daily_or_fallback(
    source_daily: List[Tuple[str, int, Optional[Dict[str, int]]]],
    index: int,
    fallback_exercise: str,
) -> str:
    if source_daily:
        return source_daily[index % len(source_daily)][0]
    return fallback_exercise


def _apply_rul_and_training_match_constraints(
    position: str,
    source_program: List[TrainingInstruction],
    total_days: int,
    start_age: float,
    weights: Dict[str, float],
    allow_boost: bool,
    boost_uses: int,
    boost_period: int,
    roll_enabled: bool = False,
    roll_block_days: int = 7,
    training_match_prelude_enabled: bool = False,
    training_match_until_age: Optional[float] = None,
) -> List[TrainingInstruction]:
    """Tving valgfri Træningskamp-indledning og/eller gentagende Rul-blok.

    Rul:
    - Finder en basisblok på roll_block_days i suffixet.
    - Gentager den som TrainingCycle.
    - Eventuel rest lægges til som almindelige faser.
    """
    position = normalize_position(position)
    total_days = max(1, int(total_days))
    prefix_days = _assistant_constraint_prefix_days(
        start_age,
        total_days,
        training_match_prelude_enabled,
        training_match_until_age,
    )
    suffix_days = max(0, total_days - prefix_days)

    fallback_scores = _exercise_scores(position, weights)
    fallback_exercise = max(fallback_scores, key=fallback_scores.get) if fallback_scores else "Træningskamp"
    source_daily = _program_to_daily(source_program)

    program: List[TrainingInstruction] = []

    if prefix_days > 0:
        prefix_daily = [
            _daily_tuple(position, "Træningskamp", weights, day, allow_boost, boost_uses, boost_period)
            for day in range(1, prefix_days + 1)
        ]
        program.extend(_compress_daily_sequence(prefix_daily))

    if suffix_days <= 0:
        return program or _compress_daily_sequence([
            _daily_tuple(position, "Træningskamp", weights, 1, allow_boost, boost_uses, boost_period)
        ])

    if not roll_enabled:
        suffix_daily = []
        for offset in range(suffix_days):
            day = prefix_days + offset + 1
            exercise = _exercise_from_daily_or_fallback(source_daily, offset, fallback_exercise)
            suffix_daily.append(_daily_tuple(position, exercise, weights, day, allow_boost, boost_uses, boost_period))
        program.extend(_compress_daily_sequence(suffix_daily))
        return program

    block_days = max(1, min(int(roll_block_days or 1), suffix_days))
    block_daily = []
    for offset in range(block_days):
        day = prefix_days + offset + 1
        exercise = _exercise_from_daily_or_fallback(source_daily, offset, fallback_exercise)
        block_daily.append(_daily_tuple(position, exercise, weights, day, allow_boost, boost_uses, boost_period))

    block_program = _compress_daily_sequence(block_daily)
    repetitions = suffix_days // block_days
    rest_days = suffix_days % block_days

    if repetitions > 0:
        program.append(
            TrainingCycle(
                name=f"Rul {block_days} dage",
                phases=block_program,
                repetitions=repetitions,
            )
        )

    if rest_days:
        rest_daily = []
        for offset in range(rest_days):
            exercise, points, distribution = block_daily[offset % len(block_daily)]
            rest_daily.append((exercise, points, None if distribution is None else dict(distribution)))
        program.extend(_compress_daily_sequence(rest_daily))

    return program


def assistant_search_training_plans_timed(
    position: str,
    start_stats: Dict[str, float],
    start_age: float,
    end_age: float,
    xp: float,
    weights: Optional[Dict[str, float]] = None,
    change_every_days: int = 3,
    search_method: str = "Beam Search",
    search_time: str = "5 min",
    xp_variance: bool = False,
    reference_training_points: int = 23,
    experience_bonus_enabled: bool = False,
    experience_bonus_ratio: float = 1.10,
    experience_bonus_knee: str = "linear",
    allow_intensity_boost: bool = False,
    intensity_boost_uses: int = 1,
    intensity_boost_period_days: int = 7,
    group_high_weighted_stats: bool = False,
    penalty_tolerance: str = "Høj",
    top_n: int = 10,
    progress_callback=None,
    stop_event=None,
    time_limit_seconds: Optional[float] = None,
    seed_programs: Optional[List[List[TrainingInstruction]]] = None,
    roll_enabled: bool = False,
    roll_block_days: int = 7,
    training_match_prelude_enabled: bool = False,
    training_match_until_age: Optional[float] = None,
    brute_force_shard_index: int = 0,
    brute_force_shard_count: int = 1,
) -> List[AssistantPlanResult]:
    """Tidsstyret Assistent-søgning med 1.64-udvidelser.

    Nye ting:
    - seed_programs kan bruges til at søge videre fra udvalgte programmer.
    - Queen Seeker cykler gennem de fem øvrige metoder og gentager fra top 10.
    """
    position = normalize_position(position)
    search_method = _canonical_search_method(search_method)
    penalty_tolerance = _canonical_penalty_tolerance(penalty_tolerance)
    start_age = float(start_age)
    end_age = float(end_age)
    if end_age <= start_age:
        end_age = start_age + 0.1
    days = max(1, days_between_ages(start_age, end_age))

    start_stats_int = _clean_start_stats(position, start_stats)
    weights_norm = _normalise_weights(position, weights)
    reference_training_points = max(20, min(24, int(reference_training_points or 23)))
    change_every_days = max(1, int(change_every_days or 1))
    boost_period = max(1, int(intensity_boost_period_days or 1))
    boost_uses = max(1, min(boost_period, int(intensity_boost_uses or 1)))
    roll_enabled = bool(roll_enabled)
    roll_block_days = max(1, int(roll_block_days or 1))
    training_match_prelude_enabled = bool(training_match_prelude_enabled)
    if training_match_until_age is not None:
        training_match_until_age = float(training_match_until_age)

    duration = float(time_limit_seconds if time_limit_seconds is not None else search_time_to_seconds(search_time))
    duration = max(0.05, duration)
    started = time.monotonic()
    deadline = started + duration
    next_progress = started

    results: List[AssistantPlanResult] = []
    seen = set()
    evaluated = 0
    loop_iterations = 0
    duplicate_iterations = 0
    # Use a string seed instead of Python's hash(), because hash randomisation differs
    # between spawned processes. The shard index keeps multi-process workers from
    # walking the same random path.
    rng = random.Random(
        f"{time.time_ns()}|{position}|{search_method}|{search_time}|"
        f"{len(seed_programs or [])}|{brute_force_shard_index}|{brute_force_shard_count}"
    )

    tm_queen_mode = search_method == TM_QUEEN_SEEKER_METHOD
    queen_like_mode = search_method == "Queen Seeker" or tm_queen_mode
    base_method = search_method if not queen_like_mode else QUEEN_SEEKER_METHODS[0]

    if tm_queen_mode:
        seed_candidates = _tm_seed_candidate_programs(
            position=position,
            total_days=days,
            start_age=start_age,
            weights=weights_norm,
            change_every_days=change_every_days,
            allow_boost=allow_intensity_boost,
            boost_uses=boost_uses,
            boost_period=boost_period,
        )
    else:
        seed_candidates = _seed_candidate_programs(
            position=position,
            days=days,
            weights=weights_norm,
            change_every_days=change_every_days,
            allow_boost=allow_intensity_boost,
            boost_uses=boost_uses,
            boost_period=boost_period,
        )

    external_seeds = [
        _apply_rul_and_training_match_constraints(
            position=position,
            source_program=_apply_intensity_schedule(position, _clone_program(program), weights_norm, allow_intensity_boost, boost_uses, boost_period),
            total_days=days,
            start_age=start_age,
            weights=weights_norm,
            allow_boost=allow_intensity_boost,
            boost_uses=boost_uses,
            boost_period=boost_period,
            roll_enabled=roll_enabled,
            roll_block_days=roll_block_days,
            training_match_prelude_enabled=training_match_prelude_enabled,
            training_match_until_age=training_match_until_age,
        )
        for program in (seed_programs or [])
        if program
    ]
    if external_seeds:
        if tm_queen_mode:
            prefix_days = _tm_prefix_days(start_age, TM_QUEEN_CUTOFF_AGES[0], days)
            seed_candidates = [("Udgangspunkt", _tm_force_prefix_program(position, external_seeds[0], days, prefix_days, weights_norm, allow_intensity_boost, boost_uses, boost_period))] + seed_candidates
        else:
            seed_candidates = [("Udgangspunkt", _clone_program(external_seeds[0]))] + seed_candidates

    seed_index = 0
    random_index = 1
    queen_index = 0
    tm_cutoff_index = 0
    working_seeds = list(external_seeds)
    queen_cycle_started = False
    brute_force_mode = search_method == "Brute Force"
    brute_force_index = max(0, int(brute_force_shard_index or 0))
    brute_force_step = max(1, int(brute_force_shard_count or 1))

    # Multi-process searches previously evaluated the same deterministic seed
    # candidates in every worker before the random phase. On short searches this
    # could make 2/4/Max feel worse and more erratic than 1 thread. Shard the
    # seed queue as well as Brute Force so extra CPU does distinct work.
    if not brute_force_mode and brute_force_step > 1 and seed_candidates:
        shard = brute_force_index % brute_force_step
        seed_candidates = seed_candidates[shard::brute_force_step]

    manual_mode = search_time == "Manuel" or brute_force_mode
    random_index = brute_force_index + 1

    def stopped() -> bool:
        return bool(stop_event is not None and stop_event.is_set())

    def publish(force: bool = False):
        nonlocal next_progress
        if progress_callback is None:
            return
        now = time.monotonic()
        if not force and now < next_progress:
            return
        next_progress = now + 1.00
        remaining = max(0.0, deadline - now)
        progress_callback({
            "elapsed": max(0.0, now - started),
            "remaining": remaining,
            "evaluated": evaluated,
            "best_rating": (results[0].uw_rating if getattr(results[0], "uw_rating", None) is not None else results[0].rating) if results else None,
            "results": list(results[:top_n]),
            "manual": manual_mode,
        })

    while (time.monotonic() < deadline or evaluated == 0) and not stopped():
        loop_iterations += 1
        if loop_iterations % 50 == 0:
            # Giv Tkinter-hovedtråden luft under lange CPU-bound søgninger.
            time.sleep(0.001)

        if len(seen) > 5000:
            # Lange Queen Seeker-kørsler må ikke opbygge et uendeligt stort dedupe-set.
            # Vi bevarer kun signaturer for de aktuelle topresultater og lader resten blive søgbart igen.
            seen = {_program_signature(result.program) for result in results[:top_n]}

        if queen_like_mode:
            current_method = QUEEN_SEEKER_METHODS[queen_index % len(QUEEN_SEEKER_METHODS)]
            # Efter første fulde runde bruges løbende top 10 som dronningens udgangspunkt.
            if queen_index > 0 and queen_index % len(QUEEN_SEEKER_METHODS) == 0:
                working_seeds = [_clone_program(result.program) for result in results[:10]]
                queen_cycle_started = True

            if seed_index < len(seed_candidates) and not queen_cycle_started:
                label, program = seed_candidates[seed_index]
                label = f"{search_method}/{current_method}: {label}"
                seed_index += 1
            elif tm_queen_mode:
                cutoff = TM_QUEEN_CUTOFF_AGES[tm_cutoff_index % len(TM_QUEEN_CUTOFF_AGES)]
                tm_cutoff_index += 1
                prefix_days = _tm_prefix_days(start_age, cutoff, days)
                suffix_days = max(1, days - prefix_days)
                if working_seeds and rng.random() < 0.85:
                    seed_program = rng.choice(working_seeds)
                    seed_suffix = _compress_daily_sequence(_program_to_daily(seed_program)[prefix_days:])
                    label, suffix = _mutate_seed_program(
                        seed_suffix, position, weights_norm, current_method,
                        allow_intensity_boost, boost_uses, boost_period, rng, random_index,
                    )
                    random_index += 1
                else:
                    label, suffix = _random_candidate_program(
                        position=position,
                        days=suffix_days,
                        weights=weights_norm,
                        change_every_days=change_every_days,
                        search_method=current_method,
                        allow_boost=allow_intensity_boost,
                        boost_uses=boost_uses,
                        boost_period=boost_period,
                        rng=rng,
                        index=random_index,
                    )
                    random_index += 1
                program = _tm_wrap_suffix_program(position, suffix, days, prefix_days, weights_norm, allow_intensity_boost, boost_uses, boost_period)
                label = f"TM til {int(cutoff)} år + Queen Seeker/{label}"
            elif working_seeds and rng.random() < 0.85:
                seed_program = rng.choice(working_seeds)
                label, program = _mutate_seed_program(
                    seed_program, position, weights_norm, current_method,
                    allow_intensity_boost, boost_uses, boost_period, rng, random_index,
                )
                label = f"Queen Seeker/{label}"
                random_index += 1
            else:
                label, program = _random_candidate_program(
                    position=position,
                    days=days,
                    weights=weights_norm,
                    change_every_days=change_every_days,
                    search_method=current_method,
                    allow_boost=allow_intensity_boost,
                    boost_uses=boost_uses,
                    boost_period=boost_period,
                    rng=rng,
                    index=random_index,
                )
                label = f"Queen Seeker/{label}"
                random_index += 1

            queen_index += 1
        else:
            current_method = search_method
            if brute_force_mode:
                brute_candidate = _brute_force_candidate_program(
                    position=position,
                    days=days,
                    weights=weights_norm,
                    change_every_days=change_every_days,
                    allow_boost=allow_intensity_boost,
                    boost_uses=boost_uses,
                    boost_period=boost_period,
                    index=brute_force_index,
                )
                brute_force_index += brute_force_step
                if brute_candidate is None:
                    break
                label, program = brute_candidate
            elif seed_index < len(seed_candidates):
                label, program = seed_candidates[seed_index]
                seed_index += 1
            elif external_seeds and rng.random() < 0.78:
                seed_program = rng.choice(external_seeds if not results else [_clone_program(r.program) for r in results[:10]])
                label, program = _mutate_seed_program(
                    seed_program, position, weights_norm, current_method,
                    allow_intensity_boost, boost_uses, boost_period, rng, random_index,
                )
                random_index += 1
            else:
                label, program = _random_candidate_program(
                    position=position,
                    days=days,
                    weights=weights_norm,
                    change_every_days=change_every_days,
                    search_method=current_method,
                    allow_boost=allow_intensity_boost,
                    boost_uses=boost_uses,
                    boost_period=boost_period,
                    rng=rng,
                    index=random_index,
                )
                random_index += 1

        program = _apply_rul_and_training_match_constraints(
            position=position,
            source_program=program,
            total_days=days,
            start_age=start_age,
            weights=weights_norm,
            allow_boost=allow_intensity_boost,
            boost_uses=boost_uses,
            boost_period=boost_period,
            roll_enabled=roll_enabled,
            roll_block_days=roll_block_days,
            training_match_prelude_enabled=training_match_prelude_enabled,
            training_match_until_age=training_match_until_age,
        )
        if roll_enabled:
            label = f"Rul {roll_block_days}d/{label}"
        if training_match_prelude_enabled and training_match_until_age is not None:
            target_year = int(training_match_until_age)
            target_day = int(round((float(training_match_until_age) - target_year) * 30))
            label = f"Træningskamp til {target_year} år {target_day} dage/{label}"

        signature = _program_signature(program)
        if signature in seen:
            duplicate_iterations += 1
            if duplicate_iterations % 100 == 0:
                time.sleep(0.001)
            publish()
            continue
        seen.add(signature)

        result = _evaluate_assistant_program(
            label=label,
            program=program,
            position=position,
            start_stats_int=start_stats_int,
            start_age=start_age,
            days=days,
            xp=xp,
            weights_norm=weights_norm,
            xp_variance=xp_variance,
            reference_training_points=reference_training_points,
            experience_bonus_enabled=experience_bonus_enabled,
            experience_bonus_ratio=experience_bonus_ratio,
            experience_bonus_knee=experience_bonus_knee,
            group_high_weighted_stats=group_high_weighted_stats,
            penalty_tolerance=penalty_tolerance,
        )
        evaluated += 1
        _insert_top_result(results, result, top_n)
        publish()

    publish(force=True)
    return results
