from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any


SHIFT_HOURS: dict[str, int] = {
    "8-6": 10,
    "8-8": 12,
    "2-12": 10,
    "O/N": 12,
}
SHIFT_NAMES: tuple[str, ...] = tuple(SHIFT_HOURS)
OVERNIGHT = "O/N"
DAY_SHIFTS: tuple[str, ...] = tuple(s for s in SHIFT_NAMES if s != OVERNIGHT)


class DoctorMode(str, Enum):
    DEFAULT = "default"
    PRESCRIBED = "prescribed"
    FIXED = "fixed"


@dataclass(frozen=True)
class RuleSpec:
    type: str
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DoctorConfig:
    name: str
    mode: DoctorMode = DoctorMode.DEFAULT
    overnight_capable: bool = False
    assignments: dict[date, str] = field(default_factory=dict)
    rules: tuple[RuleSpec, ...] = ()
    target_hours: int | None = None
    use_default_rest_rules: bool = True
    max_weekend_pairs: int | None = None

    def is_protected(self, day: date) -> bool:
        return self.mode in {DoctorMode.FIXED, DoctorMode.PRESCRIBED} and day in self.assignments


@dataclass(frozen=True)
class DefaultRules:
    max_consecutive_days: int = 3
    rolling_window_days: int = 5
    max_shifts_in_rolling_window: int = 3
    max_overnights: int = 6
    max_weekend_pairs: int = 1
    forbid_86_after_88: bool = True
    recovery_after_212_days: int = 2


@dataclass(frozen=True)
class QualityWeights:
    hour_balance: int = 1
    weekend_single: int = 10_000
    isolated_workday: int = 20


@dataclass(frozen=True)
class SolverSettings:
    max_time_per_phase_seconds: float = 60.0
    workers: int = 8
    random_seed: int = 2026
    log_progress: bool = False


@dataclass(frozen=True)
class ScheduleConfig:
    start: date
    end: date
    doctors: tuple[DoctorConfig, ...]
    base_target_hours: int = 120
    prorate_after_unavailable_days: int = 3
    default_rules: DefaultRules = field(default_factory=DefaultRules)
    quality_weights: QualityWeights = field(default_factory=QualityWeights)
    solver: SolverSettings = field(default_factory=SolverSettings)

    @property
    def dates(self) -> tuple[date, ...]:
        from datetime import timedelta

        return tuple(
            self.start + timedelta(days=offset)
            for offset in range((self.end - self.start).days + 1)
        )


@dataclass(frozen=True)
class HistorySchedule:
    source: Path
    dates: tuple[date, ...]
    assignments: dict[str, dict[date, str]]
    open_shifts: dict[date, tuple[str, ...]]
    warnings: tuple[str, ...] = ()

    def shift_for(self, doctor: str, day: date) -> str | None:
        return self.assignments.get(doctor, {}).get(day)

    def overnight_count(self, doctor: str) -> int:
        return sum(shift == OVERNIGHT for shift in self.assignments.get(doctor, {}).values())


@dataclass(frozen=True)
class PhaseReport:
    name: str
    status: str
    objective: float
    wall_time_seconds: float


@dataclass(frozen=True)
class ScheduleResult:
    assignments: dict[str, dict[date, str]]
    open_shifts: dict[date, tuple[str, ...]]
    target_hours: dict[str, int]
    history_overnights: dict[str, int]
    phase_reports: tuple[PhaseReport, ...]
