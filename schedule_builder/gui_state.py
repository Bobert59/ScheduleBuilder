from __future__ import annotations

import copy
from datetime import date, timedelta
from typing import Any


RULE_LABELS = {
    "allowed_weekdays": "Allowed weekdays",
    "unavailable_dates": "Unavailable dates",
    "start_date": "Join/start date",
    "end_date": "End/leave date",
    "forbidden_shifts": "Forbidden shifts",
    "allowed_shifts": "Allowed shifts",
    "forbidden_shift_weekdays": "Forbid shifts on weekdays",
    "max_total_shifts": "Maximum total shifts",
    "max_overnights": "Maximum total overnights",
    "max_overnight_block_length": "Maximum overnight block",
    "max_weekend_days": "Maximum weekend days",
    "max_consecutive_days": "Maximum consecutive days",
    "rolling_limit": "Rolling shift limit",
}

WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def new_config() -> dict[str, Any]:
    start = date.today() + timedelta(days=1)
    end = start + timedelta(days=27)
    return {
        "schedule": {"start": start.isoformat(), "end": end.isoformat()},
        "base_target_hours": 120,
        "prorate_after_unavailable_days": 3,
        "default_rules": {
            "max_consecutive_days": 3,
            "rolling_window_days": 5,
            "max_shifts_in_rolling_window": 3,
            "max_overnights": 6,
            "max_weekend_pairs": 1,
            "forbid_86_after_88": True,
            "recovery_after_212_days": 2,
        },
        "quality_weights": {
            "hour_balance": 1,
            "weekend_single": 10_000,
            "isolated_workday": 20,
        },
        "solver": {
            "max_time_per_phase_seconds": 60,
            "workers": 6,
            "random_seed": 2026,
            "log_progress": False,
        },
        "doctors": [],
    }


def new_doctor(name: str = "New Doctor") -> dict[str, Any]:
    return {
        "name": name,
        "mode": "default",
        "overnight_capable": False,
        "time_off": [],
        "assignments": {},
        "rules": [],
    }


def normalize_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Fill GUI defaults without removing fields understood by the backend."""
    baseline = new_config()
    result = copy.deepcopy(raw)
    result.setdefault("schedule", baseline["schedule"])
    for key in ("base_target_hours", "prorate_after_unavailable_days"):
        result.setdefault(key, baseline[key])
    for section in ("default_rules", "quality_weights", "solver"):
        values = result.setdefault(section, {})
        for key, value in baseline[section].items():
            values.setdefault(key, value)
    doctors = result.setdefault("doctors", [])
    for doctor in doctors:
        doctor.setdefault("mode", "default")
        doctor.setdefault("overnight_capable", False)
        doctor.setdefault("time_off", [])
        doctor.setdefault("assignments", {})
        doctor.setdefault("rules", [])
    return result


def time_off_label(item: Any) -> str:
    if isinstance(item, dict):
        return f"{item.get('start', '?')} through {item.get('end', '?')}"
    return str(item)


def rule_label(rule: dict[str, Any]) -> str:
    rule_type = str(rule.get("type", ""))
    title = RULE_LABELS.get(rule_type, rule_type or "Unknown rule")
    if rule_type == "allowed_weekdays":
        detail = ", ".join(str(day) for day in rule.get("weekdays", []))
    elif rule_type == "unavailable_dates":
        detail = f"{len(rule.get('dates', []))} date(s)"
    elif rule_type in {"start_date", "end_date"}:
        detail = str(rule.get("date", ""))
    elif rule_type in {"forbidden_shifts", "allowed_shifts"}:
        detail = ", ".join(rule.get("shifts", []))
    elif rule_type == "forbidden_shift_weekdays":
        detail = f"{', '.join(rule.get('shifts', []))} on {', '.join(rule.get('weekdays', []))}"
    elif rule_type == "rolling_limit":
        detail = f"max {rule.get('max_shifts', '?')} in {rule.get('window_days', '?')} days"
    else:
        detail = str(rule.get("value", ""))
    return f"{title}: {detail}" if detail else title


def suggested_output_name(config: dict[str, Any]) -> str:
    try:
        start = date.fromisoformat(config["schedule"]["start"])
        end = date.fromisoformat(config["schedule"]["end"])
    except (KeyError, TypeError, ValueError):
        return "Doctor_Schedule.xlsx"
    return f"{start:%b%d}_{end:%b%d}_Schedule.xlsx"

