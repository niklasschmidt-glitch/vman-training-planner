from typing import Dict

from .config import XP_TO_NEXT, POSITION_STATS, POSITION_GROUP, EXERCISE_STATS_BY_GROUP, POSITION_ALIASES
from .models import PlayerState


def normalize_position(position: str) -> str:
    return POSITION_ALIASES.get(position, position)


def make_equal_start(value: int = 3, position: str = "Forsvar") -> Dict[str, int]:
    position = normalize_position(position)
    return {stat: value for stat in POSITION_STATS[position]}


def exercise_stats_for_position(position: str, exercise: str):
    position = normalize_position(position)
    group = POSITION_GROUP[position]
    exercises = EXERCISE_STATS_BY_GROUP[group]
    if exercise not in exercises:
        raise ValueError(f"Øvelsen '{exercise}' findes ikke for positionen '{position}'.")
    return exercises[exercise]


def exercises_for_position(position: str):
    position = normalize_position(position)
    group = POSITION_GROUP[position]
    return EXERCISE_STATS_BY_GROUP[group]


def default_training_match_distribution(position: str) -> Dict[str, int]:
    position = normalize_position(position)
    return {stat: 1 for stat in POSITION_STATS[position]}


def validate_distribution(
    position: str,
    exercise: str,
    distribution: Dict[str, int],
    strict_total: bool = True,
    min_points: int = 2,
    max_points: int = 10,
    required_total: int = 23,
) -> None:
    position = normalize_position(position)
    exercises = exercises_for_position(position)

    if exercise not in exercises:
        raise ValueError(f"Ukendt øvelse for {position}: {exercise}")

    if exercise == "Træningskamp":
        return

    allowed = set(exercises[exercise])
    used = set(distribution.keys())

    if used != allowed:
        missing = sorted(allowed - used)
        extra = sorted(used - allowed)
        raise ValueError(
            f"Fordelingen matcher ikke øvelsen '{exercise}'. "
            f"Mangler={missing}, ekstra={extra}"
        )

    total = sum(distribution.values())
    if strict_total and total != required_total:
        raise ValueError(f"Pointfordelingen skal summere til {required_total}, men summerer til {total}.")

    for stat, points in distribution.items():
        if points < min_points or points > max_points:
            raise ValueError(
                f"{stat} har {points} point. Tilladt interval er {min_points}-{max_points}."
            )


def penalty_factor_for_stat(
    state: PlayerState,
    stat: str,
    exempt: bool = False,
    stat_mode: str = "fractional",
) -> float:
    if exempt:
        return 1.0

    avg = state.average_stat(mode=stat_mode)
    current = state.stat_value(stat, mode=stat_mode)

    if avg <= 0:
        return 1.0

    percent_above = (current / avg - 1.0) * 100.0

    if percent_above <= 15.0:
        return 1.0

    reduction_percent = (percent_above - 15.0) / 3.0
    factor = 1.0 - reduction_percent / 100.0

    return max(0.0, factor)


def add_xp_to_stat(state: PlayerState, stat: str, xp: float) -> None:
    if state.stats[stat] >= 100:
        state.stats[stat] = 100
        state.progress_xp[stat] = 0.0
        return

    state.progress_xp[stat] += xp

    while state.stats[stat] < 100:
        needed = XP_TO_NEXT[state.stats[stat]]

        if state.progress_xp[stat] < needed:
            break

        state.progress_xp[stat] -= needed
        state.stats[stat] += 1

    if state.stats[stat] >= 100:
        state.stats[stat] = 100
        state.progress_xp[stat] = 0.0
