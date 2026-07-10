from typing import Dict, List, Optional, Tuple
import copy

from .config import POSITION_STATS, POSITION_WEIGHTS
from .models import PlayerState, TrainingInstruction, TrainingCycle, TrainingDayResult
from .rules import (
    make_equal_start,
    default_training_match_distribution,
    validate_distribution,
    penalty_factor_for_stat,
    add_xp_to_stat,
    exercise_stats_for_position,
    normalize_position,
)


def days_between_ages(start_age: float, end_age: float) -> int:
    return int(round((end_age - start_age) * 30))


def calculate_end_age(start_age: float, total_days: int) -> float:
    return start_age + total_days / 30.0


def calculate_rating(
    state: PlayerState,
    position: str,
    stat_mode: str = "fractional",
) -> float:
    position = normalize_position(position)
    if position not in POSITION_WEIGHTS:
        raise ValueError(f"Ukendt position: {position}")

    weights = POSITION_WEIGHTS[position]
    total_weight = sum(weights.values())
    values = state.stat_values(mode=stat_mode)

    return sum(values[stat] * weight for stat, weight in weights.items()) / total_weight


def knee_progress(progress: float, knee: str = "linear") -> float:
    progress = max(0.0, min(1.0, float(progress)))

    if knee in {"early", "front", "tidlig"}:
        # Tidlig stigning og senere stagnering.
        return 1.0 - (1.0 - progress) ** 2

    if knee in {"late", "back", "sen"}:
        # Sen stigning.
        return progress ** 2

    # Lineær stigning.
    return progress


def absolute_experience_bonus_at_age(
    age: float,
    ratio: float = 1.10,
    knee: str = "linear",
) -> float:
    """
    Absolut, teoretisk bonusniveau fra 15 til 27 år.

    Dette er ikke direkte multiplier i simuleringen, fordi brugerens
    indtastede XP ved startalderen allerede regnes som spillerens aktuelle
    niveau. Funktionen bruges derfor til at beregne den resterende relative
    bonus fra startalderen.
    """
    ratio = max(1.0, float(ratio))
    age = float(age)

    progress = (age - 15.0) / 12.0
    progress = max(0.0, min(1.0, progress))
    shaped_progress = knee_progress(progress, knee=knee)

    return 1.0 + (ratio - 1.0) * shaped_progress


def experience_bonus_multiplier(
    day: int,
    enabled: bool = False,
    ratio: float = 1.10,
    knee: str = "linear",
    start_age: float = 15.0,
) -> float:
    """
    Korrigeret relativ restbonus.

    Ratio beskriver samlet potentiel bonus fra 15 til 27 år.
    Brugerens indtastede XP ved startalderen regnes som spillerens aktuelle
    niveau. Derfor giver simuleringen kun resten:

        multiplier = absolut_bonus(aktuel_alder) / absolut_bonus(startalder)

    Eksempel:
    Startalder 24, ratio 1.30, lineær:
    absolut_bonus(24) = 1.27
    absolut_bonus(27) = 1.30
    multiplier ved 27 beregnes som resterende relativ bonus
    """
    if not enabled:
        return 1.0

    start_age = float(start_age)
    if start_age >= 27.0:
        return 1.0

    current_age = min(27.0, start_age + float(day) / 30.0)

    base_bonus = absolute_experience_bonus_at_age(start_age, ratio=ratio, knee=knee)
    current_bonus = absolute_experience_bonus_at_age(current_age, ratio=ratio, knee=knee)

    if base_bonus <= 0:
        return 1.0

    return max(1.0, current_bonus / base_bonus)


def effective_xp_for_intensity(
    xp_at_reference_intensity: float,
    training_points: int,
    reference_training_points: int = 23,
    experience_multiplier: float = 1.0,
) -> float:
    """
    XP-feltet i GUI'en angiver XP ved den valgte reference-intensitet.
    Andre intensiteter skaleres lineært ud fra reference-intensiteten.
    Erfaringsbonus ganges ovenpå, hvis den er slået til.
    """
    if reference_training_points <= 0:
        raise ValueError("reference_training_points skal være positiv.")
    return xp_at_reference_intensity * experience_multiplier * training_points / reference_training_points


def train_one_day(
    state: PlayerState,
    day: int,
    position: str,
    exercise: str,
    xp: float,
    distribution: Optional[Dict[str, int]] = None,
    start_age: float = 15.0,
    stat_mode_for_penalty: str = "fractional",
    rating_stat_mode: str = "fractional",
    validate: bool = True,
    training_points: int = 23,
    reference_training_points: int = 23,
    experience_bonus_enabled: bool = False,
    experience_bonus_ratio: float = 1.10,
    experience_bonus_knee: str = "linear",

    experience_bonus_start_age: float = 15.0,
) -> TrainingDayResult:
    position = normalize_position(position)
    trained_stats = exercise_stats_for_position(position, exercise)
    experience_multiplier = experience_bonus_multiplier(
        day,
        enabled=experience_bonus_enabled,
        ratio=experience_bonus_ratio,
        knee=experience_bonus_knee,
        start_age=experience_bonus_start_age,
    )
    effective_session_xp = effective_xp_for_intensity(
        xp,
        training_points,
        reference_training_points=reference_training_points,
        experience_multiplier=experience_multiplier,
    )

    if exercise == "Træningskamp":
        distribution_used = default_training_match_distribution(position)
        raw_xp_by_stat = {
            stat: effective_session_xp / len(POSITION_STATS[position])
            for stat in trained_stats
        }
    else:
        if distribution is None:
            raise ValueError(f"Øvelsen '{exercise}' kræver en pointfordeling.")

        if validate:
            validate_distribution(position, exercise, distribution, required_total=training_points)

        total_points = sum(distribution.values())
        distribution_used = dict(distribution)
        raw_xp_by_stat = {
            stat: effective_session_xp * distribution[stat] / total_points
            for stat in trained_stats
        }

    before = state.stat_values(mode="fractional")

    penalty_factor_by_stat = {}
    effective_xp_by_stat = {}

    for stat in trained_stats:
        if stat_mode_for_penalty == "none":
            factor = 1.0
        else:
            factor = penalty_factor_for_stat(
                state,
                stat,
                exempt=(exercise == "Træningskamp"),
                stat_mode=stat_mode_for_penalty,
            )
        penalty_factor_by_stat[stat] = factor
        effective_xp_by_stat[stat] = raw_xp_by_stat[stat] * factor

    for stat, effective_xp in effective_xp_by_stat.items():
        add_xp_to_stat(state, stat, effective_xp)

    after = state.stat_values(mode="fractional")
    age = start_age + day / 30.0
    rating = calculate_rating(state, position, stat_mode=rating_stat_mode)

    return TrainingDayResult(
        day=day,
        age=age,
        exercise=exercise,
        distribution=distribution_used,
        before_stats=before,
        after_stats=after,
        raw_xp_by_stat=raw_xp_by_stat,
        effective_xp_by_stat=effective_xp_by_stat,
        penalty_factor_by_stat=penalty_factor_by_stat,
        rating=rating,
        training_points=training_points,
    )



def expand_program(program) -> List[Tuple[str, Optional[Dict[str, int]], int]]:
    days = []

    def add_instruction(instruction):
        if isinstance(instruction, TrainingCycle):
            if instruction.repetitions <= 0:
                raise ValueError("Cyklus-gentagelser skal være positive.")
            if instruction.base_days <= 0:
                raise ValueError("Cyklus skal indeholde mindst én træningsdag.")

            for _ in range(instruction.repetitions):
                for phase in instruction.phases:
                    add_instruction(phase)
            return

        if instruction.days <= 0:
            raise ValueError("days skal være positivt.")
        if instruction.training_points < 20 or instruction.training_points > 24:
            raise ValueError("training_points skal være mellem 20 og 24.")
        for _ in range(instruction.days):
            days.append((instruction.exercise, instruction.distribution, instruction.training_points))

    for instruction in program:
        add_instruction(instruction)

    return days


def simulate_program(
    start_stats: Dict[str, int],
    program: List[TrainingInstruction],
    position: str,
    xp: float = 205,
    start_age: float = 15.0,
    keep_history: bool = True,
    stat_mode_for_penalty: str = "fractional",
    rating_stat_mode: str = "fractional",
    reference_training_points: int = 23,
    experience_bonus_enabled: bool = False,
    experience_bonus_ratio: float = 1.10,
    experience_bonus_knee: str = "linear",

    experience_bonus_start_age: Optional[float] = None,
) -> Tuple[PlayerState, List[TrainingDayResult]]:
    position = normalize_position(position)
    state = PlayerState(copy.deepcopy(start_stats), stat_names=POSITION_STATS[position])
    expanded = expand_program(program)

    history: List[TrainingDayResult] = []

    for day_index, (exercise, distribution, training_points) in enumerate(expanded, start=1):
        result = train_one_day(
            state=state,
            day=day_index,
            position=position,
            exercise=exercise,
            xp=xp,
            distribution=distribution,
            start_age=start_age,
            stat_mode_for_penalty=stat_mode_for_penalty,
            rating_stat_mode=rating_stat_mode,
            training_points=training_points,
            reference_training_points=reference_training_points,
            experience_bonus_enabled=experience_bonus_enabled,
            experience_bonus_ratio=experience_bonus_ratio,
            experience_bonus_knee=experience_bonus_knee,
            experience_bonus_start_age=experience_bonus_start_age,
        )
        if keep_history:
            history.append(result)

    return state, history


def summarize_state(
    state: PlayerState,
    position: str,
    stat_mode: str = "fractional",
) -> Dict[str, float]:
    position = normalize_position(position)
    output = state.stat_values(mode=stat_mode)
    output["Gennemsnit"] = state.average_stat(mode=stat_mode)
    output[f"Vurderingstal_{position}"] = calculate_rating(
        state,
        position,
        stat_mode=stat_mode,
    )
    return output
