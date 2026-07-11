from dataclasses import dataclass, field
from typing import Dict, Optional, List, Union
import copy

from .config import XP_TO_NEXT


@dataclass
class PlayerState:
    stats: Dict[str, int]
    stat_names: List[str]
    progress_xp: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        missing = [stat for stat in self.stat_names if stat not in self.stats]
        if missing:
            raise ValueError(f"Mangler startværdi for: {missing}")

        unknown = [stat for stat in self.stats if stat not in self.stat_names]
        if unknown:
            raise ValueError(f"Ukendte stats for denne position: {unknown}")

        for stat, value in self.stats.items():
            if not isinstance(value, int):
                raise TypeError(f"{stat} skal være et heltal.")
            if value < 0 or value > 100:
                raise ValueError(f"{stat} skal være mellem 0 og 100.")

        if not self.progress_xp:
            self.progress_xp = {stat: 0.0 for stat in self.stat_names}

        for stat in self.stat_names:
            self.progress_xp.setdefault(stat, 0.0)

    def clone(self) -> "PlayerState":
        return PlayerState(
            stats=copy.deepcopy(self.stats),
            stat_names=list(self.stat_names),
            progress_xp=copy.deepcopy(self.progress_xp),
        )

    def stat_value(self, stat: str, mode: str = "fractional") -> float:
        if mode == "integer":
            return float(self.stats[stat])

        if mode != "fractional":
            raise ValueError("mode skal være 'fractional' eller 'integer'.")

        value = self.stats[stat]
        if value >= 100:
            return 100.0

        needed = XP_TO_NEXT[value]
        return value + self.progress_xp[stat] / needed

    def stat_values(self, mode: str = "fractional") -> Dict[str, float]:
        return {stat: self.stat_value(stat, mode=mode) for stat in self.stat_names}

    def average_stat(self, mode: str = "fractional") -> float:
        values = self.stat_values(mode=mode)
        return sum(values.values()) / len(values)


@dataclass(frozen=True)
class TrainingInstruction:
    days: int
    exercise: str
    distribution: Optional[Dict[str, int]] = None
    training_points: int = 23



@dataclass(frozen=True)
class TrainingCycle:
    name: str
    phases: List["TrainingInstruction"]
    repetitions: int = 1

    @property
    def base_days(self) -> int:
        return sum(phase.days for phase in self.phases)

    @property
    def days(self) -> int:
        return self.base_days * self.repetitions


@dataclass
class TrainingDayResult:
    day: int
    age: float
    exercise: str
    distribution: Dict[str, int]
    before_stats: Dict[str, float]
    after_stats: Dict[str, float]
    raw_xp_by_stat: Dict[str, float]
    effective_xp_by_stat: Dict[str, float]
    penalty_factor_by_stat: Dict[str, float]
    rating: Optional[float]
    training_points: int
