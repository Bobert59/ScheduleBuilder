from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from .domain import (
    SHIFT_NAMES,
    DefaultRules,
    DoctorConfig,
    DoctorMode,
    QualityWeights,
    RuleSpec,
    ScheduleConfig,
    SolverSettings,
)
from .errors import ConfigurationError


WEEKDAY_NAMES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}

SUPPORTED_RULES = {
    "allowed_weekdays",
    "unavailable_dates",
    "start_date",
    "end_date",
    "forbidden_shifts",
    "forbidden_shift_weekdays",
    "allowed_shifts",
    "max_total_shifts",
    "max_overnights",
    "max_weekend_days",
    "max_consecutive_days",
    "rolling_limit",
}


def _date(value: Any, label: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ConfigurationError(f"{label} must be an ISO date (YYYY-MM-DD), got {value!r}.") from exc


def _positive_int(value: Any, label: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool):
        raise ConfigurationError(f"{label} must be an integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{label} must be an integer.") from exc
    minimum = 0 if allow_zero else 1
    if parsed < minimum:
        raise ConfigurationError(f"{label} must be at least {minimum}.")
    return parsed


def _normalize_rule(raw: Any, doctor_name: str, index: int) -> RuleSpec:
    if not isinstance(raw, dict):
        raise ConfigurationError(f"Rule {index + 1} for {doctor_name} must be an object.")
    rule_type = str(raw.get("type", "")).strip().lower()
    if rule_type not in SUPPORTED_RULES:
        allowed = ", ".join(sorted(SUPPORTED_RULES))
        raise ConfigurationError(
            f"Unknown rule type {rule_type!r} for {doctor_name}. Supported types: {allowed}."
        )
    values = {key: value for key, value in raw.items() if key != "type"}

    if rule_type == "allowed_weekdays":
        weekdays = values.get("weekdays")
        if not isinstance(weekdays, list) or not weekdays:
            raise ConfigurationError(f"allowed_weekdays for {doctor_name} needs a non-empty weekdays list.")
        try:
            values["weekdays"] = sorted({WEEKDAY_NAMES[str(day).strip().lower()] for day in weekdays})
        except KeyError as exc:
            raise ConfigurationError(f"Invalid weekday {exc.args[0]!r} for {doctor_name}.") from exc
    elif rule_type == "unavailable_dates":
        dates = values.get("dates", [])
        if not isinstance(dates, list):
            raise ConfigurationError(f"unavailable_dates for {doctor_name} needs a dates list.")
        values["dates"] = [_date(day, f"unavailable date for {doctor_name}") for day in dates]
    elif rule_type in {"start_date", "end_date"}:
        values["date"] = _date(values.get("date"), f"{rule_type} for {doctor_name}")
    elif rule_type in {"forbidden_shifts", "allowed_shifts"}:
        shifts = values.get("shifts")
        if not isinstance(shifts, list) or not shifts:
            raise ConfigurationError(f"{rule_type} for {doctor_name} needs a non-empty shifts list.")
        unknown = set(shifts) - set(SHIFT_NAMES)
        if unknown:
            raise ConfigurationError(f"Unknown shifts for {doctor_name}: {sorted(unknown)}.")
        values["shifts"] = list(dict.fromkeys(shifts))
    elif rule_type == "forbidden_shift_weekdays":
        shifts = values.get("shifts")
        weekdays = values.get("weekdays")
        if not isinstance(shifts, list) or not shifts:
            raise ConfigurationError(
                f"forbidden_shift_weekdays for {doctor_name} needs a non-empty shifts list."
            )
        unknown = set(shifts) - set(SHIFT_NAMES)
        if unknown:
            raise ConfigurationError(f"Unknown shifts for {doctor_name}: {sorted(unknown)}.")
        if not isinstance(weekdays, list) or not weekdays:
            raise ConfigurationError(
                f"forbidden_shift_weekdays for {doctor_name} needs a non-empty weekdays list."
            )
        try:
            values["weekdays"] = sorted(
                {WEEKDAY_NAMES[str(day).strip().lower()] for day in weekdays}
            )
        except KeyError as exc:
            raise ConfigurationError(f"Invalid weekday {exc.args[0]!r} for {doctor_name}.") from exc
        values["shifts"] = list(dict.fromkeys(shifts))
    elif rule_type in {
        "max_total_shifts",
        "max_overnights",
        "max_weekend_days",
        "max_consecutive_days",
    }:
        values["value"] = _positive_int(values.get("value"), f"{rule_type} for {doctor_name}", allow_zero=True)
    elif rule_type == "rolling_limit":
        values["window_days"] = _positive_int(
            values.get("window_days"), f"rolling_limit.window_days for {doctor_name}"
        )
        values["max_shifts"] = _positive_int(
            values.get("max_shifts"), f"rolling_limit.max_shifts for {doctor_name}", allow_zero=True
        )
        if values["max_shifts"] > values["window_days"]:
            raise ConfigurationError(f"rolling_limit max_shifts cannot exceed window_days for {doctor_name}.")

    return RuleSpec(type=rule_type, values=values)


def _doctor(raw: Any, start: date, end: date, index: int) -> DoctorConfig:
    if not isinstance(raw, dict):
        raise ConfigurationError(f"Doctor entry {index + 1} must be an object.")
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ConfigurationError(f"Doctor entry {index + 1} has no name.")
    if name.upper().startswith("OPEN"):
        raise ConfigurationError(f"{name!r} is reserved; OPEN rows are created automatically.")
    try:
        mode = DoctorMode(str(raw.get("mode", "default")).strip().lower())
    except ValueError as exc:
        raise ConfigurationError(f"Invalid mode for {name}; use default, prescribed, or fixed.") from exc

    assignments_raw = raw.get("assignments", {})
    if not isinstance(assignments_raw, dict):
        raise ConfigurationError(f"assignments for {name} must be an object keyed by ISO date.")
    assignments: dict[date, str] = {}
    for raw_day, raw_shift in assignments_raw.items():
        day = _date(raw_day, f"assignment date for {name}")
        shift = str(raw_shift).strip()
        if shift not in SHIFT_NAMES:
            raise ConfigurationError(f"Unknown assignment shift {shift!r} for {name} on {day}.")
        if not start <= day <= end:
            raise ConfigurationError(f"Assignment for {name} on {day} is outside the schedule window.")
        assignments[day] = shift
    if mode == DoctorMode.DEFAULT and assignments:
        raise ConfigurationError(f"{name} has assignments but mode is default; use prescribed or fixed.")
    overnight_capable = bool(raw.get("overnight_capable", False))
    if not overnight_capable and any(shift == "O/N" for shift in assignments.values()):
        raise ConfigurationError(f"{name} has a fixed/prescribed O/N but is not overnight_capable.")

    rules_raw = raw.get("rules", [])
    if not isinstance(rules_raw, list):
        raise ConfigurationError(f"rules for {name} must be a list.")
    rules = tuple(_normalize_rule(rule, name, i) for i, rule in enumerate(rules_raw))
    target = raw.get("target_hours")
    if target is not None:
        target = _positive_int(target, f"target_hours for {name}", allow_zero=True)
    max_weekend_pairs = raw.get("max_weekend_pairs")
    if max_weekend_pairs is not None:
        max_weekend_pairs = _positive_int(
            max_weekend_pairs, f"max_weekend_pairs for {name}", allow_zero=True
        )
    return DoctorConfig(
        name=name,
        mode=mode,
        overnight_capable=overnight_capable,
        assignments=assignments,
        rules=rules,
        target_hours=target,
        use_default_rest_rules=bool(raw.get("use_default_rest_rules", True)),
        max_weekend_pairs=max_weekend_pairs,
    )


def load_config(path: str | Path) -> ScheduleConfig:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"Could not read configuration {source}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Invalid JSON in {source} at line {exc.lineno}: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("The configuration root must be an object.")

    schedule = raw.get("schedule", {})
    if not isinstance(schedule, dict):
        raise ConfigurationError("schedule must be an object.")
    start = _date(schedule.get("start"), "schedule.start")
    end = _date(schedule.get("end"), "schedule.end")
    if end < start:
        raise ConfigurationError("schedule.end cannot be before schedule.start.")

    doctors_raw = raw.get("doctors")
    if not isinstance(doctors_raw, list) or not doctors_raw:
        raise ConfigurationError("doctors must be a non-empty list.")
    doctors = tuple(_doctor(item, start, end, i) for i, item in enumerate(doctors_raw))
    names = [doctor.name.casefold() for doctor in doctors]
    if len(names) != len(set(names)):
        raise ConfigurationError("Doctor names must be unique (case-insensitive).")

    occupied: dict[tuple[date, str], str] = {}
    for doctor in doctors:
        for day, shift in doctor.assignments.items():
            key = (day, shift)
            if key in occupied:
                raise ConfigurationError(
                    f"{doctor.name} and {occupied[key]} are both assigned {shift} on {day}."
                )
            occupied[key] = doctor.name

    defaults_raw = raw.get("default_rules", {})
    quality_raw = raw.get("quality_weights", {})
    solver_raw = raw.get("solver", {})
    try:
        defaults = DefaultRules(**defaults_raw)
        quality = QualityWeights(**quality_raw)
        solver = SolverSettings(**solver_raw)
    except TypeError as exc:
        raise ConfigurationError(f"Unknown or missing settings field: {exc}") from exc
    if defaults.max_consecutive_days < 1:
        raise ConfigurationError("default_rules.max_consecutive_days must be positive.")
    if defaults.max_shifts_in_rolling_window > defaults.rolling_window_days:
        raise ConfigurationError("Default rolling maximum cannot exceed its window length.")
    if solver.max_time_per_phase_seconds <= 0 or solver.workers <= 0:
        raise ConfigurationError("Solver time and worker count must be positive.")

    return ScheduleConfig(
        start=start,
        end=end,
        doctors=doctors,
        base_target_hours=_positive_int(raw.get("base_target_hours", 120), "base_target_hours", allow_zero=True),
        prorate_after_unavailable_days=_positive_int(
            raw.get("prorate_after_unavailable_days", 3),
            "prorate_after_unavailable_days",
            allow_zero=True,
        ),
        default_rules=defaults,
        quality_weights=quality,
        solver=solver,
    )
