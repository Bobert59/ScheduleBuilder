from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

from ortools.sat.python import cp_model

from .domain import DoctorConfig, RuleSpec, SHIFT_NAMES


@dataclass
class RuleContext:
    model: cp_model.CpModel
    dates: tuple[date, ...]
    doctor: DoctorConfig
    shift_index: dict[str, int]
    assignments: dict[tuple[int, int], cp_model.IntVar]
    work: dict[int, cp_model.IntVar]
    protected_days: set[int]

    def auto_days(self) -> list[int]:
        return [d for d in range(len(self.dates)) if d not in self.protected_days]

    def set_day_off(self, day_index: int) -> None:
        if day_index not in self.protected_days:
            self.model.add(self.work[day_index] == 0)


RuleHandler = Callable[[RuleContext, RuleSpec], None]
RULE_HANDLERS: dict[str, RuleHandler] = {}


def register(name: str) -> Callable[[RuleHandler], RuleHandler]:
    def decorator(handler: RuleHandler) -> RuleHandler:
        RULE_HANDLERS[name] = handler
        return handler

    return decorator


@register("allowed_weekdays")
def _allowed_weekdays(context: RuleContext, rule: RuleSpec) -> None:
    allowed = set(rule.values["weekdays"])
    for d, day in enumerate(context.dates):
        if day.weekday() not in allowed:
            context.set_day_off(d)


@register("unavailable_dates")
def _unavailable_dates(context: RuleContext, rule: RuleSpec) -> None:
    unavailable = set(rule.values["dates"])
    for d, day in enumerate(context.dates):
        if day in unavailable:
            context.set_day_off(d)


@register("start_date")
def _start_date(context: RuleContext, rule: RuleSpec) -> None:
    start = rule.values["date"]
    for d, day in enumerate(context.dates):
        if day < start:
            context.set_day_off(d)


@register("end_date")
def _end_date(context: RuleContext, rule: RuleSpec) -> None:
    end = rule.values["date"]
    for d, day in enumerate(context.dates):
        if day > end:
            context.set_day_off(d)


@register("forbidden_shifts")
def _forbidden_shifts(context: RuleContext, rule: RuleSpec) -> None:
    for shift in rule.values["shifts"]:
        s = context.shift_index[shift]
        for d in context.auto_days():
            context.model.add(context.assignments[d, s] == 0)


@register("allowed_shifts")
def _allowed_shifts(context: RuleContext, rule: RuleSpec) -> None:
    forbidden = set(SHIFT_NAMES) - set(rule.values["shifts"])
    _forbidden_shifts(context, RuleSpec("forbidden_shifts", {"shifts": list(forbidden)}))


@register("forbidden_shift_weekdays")
def _forbidden_shift_weekdays(context: RuleContext, rule: RuleSpec) -> None:
    weekdays = set(rule.values["weekdays"])
    for d in context.auto_days():
        if context.dates[d].weekday() not in weekdays:
            continue
        for shift in rule.values["shifts"]:
            context.model.add(context.assignments[d, context.shift_index[shift]] == 0)


@register("max_total_shifts")
def _max_total_shifts(context: RuleContext, rule: RuleSpec) -> None:
    context.model.add(sum(context.work[d] for d in context.auto_days()) <= rule.values["value"])


@register("max_overnights")
def _max_overnights(context: RuleContext, rule: RuleSpec) -> None:
    overnight = context.shift_index["O/N"]
    context.model.add(
        sum(context.assignments[d, overnight] for d in context.auto_days()) <= rule.values["value"]
    )


@register("max_overnight_block_length")
def _max_overnight_block_length(_context: RuleContext, _rule: RuleSpec) -> None:
    # The optimizer applies this across imported history and the new schedule.
    return


@register("max_weekend_days")
def _max_weekend_days(context: RuleContext, rule: RuleSpec) -> None:
    context.model.add(
        sum(
            context.work[d]
            for d in context.auto_days()
            if context.dates[d].weekday() >= 5
        )
        <= rule.values["value"]
    )


@register("max_consecutive_days")
def _max_consecutive_days(context: RuleContext, rule: RuleSpec) -> None:
    maximum = rule.values["value"]
    width = maximum + 1
    for start in range(len(context.dates) - width + 1):
        indices = range(start, start + width)
        protected = sum(d in context.protected_days for d in indices)
        context.model.add(sum(context.work[d] for d in indices) <= maximum + protected)


@register("rolling_limit")
def _rolling_limit(context: RuleContext, rule: RuleSpec) -> None:
    width = rule.values["window_days"]
    maximum = rule.values["max_shifts"]
    for start in range(len(context.dates) - width + 1):
        indices = range(start, start + width)
        protected = sum(d in context.protected_days for d in indices)
        context.model.add(sum(context.work[d] for d in indices) <= maximum + protected)


def apply_rule(context: RuleContext, rule: RuleSpec) -> None:
    RULE_HANDLERS[rule.type](context, rule)


def unavailable_dates_for(doctor: DoctorConfig, dates: tuple[date, ...]) -> set[date]:
    """Return dates completely blocked by availability-oriented rules."""
    unavailable: set[date] = set()
    for rule in doctor.rules:
        if rule.type == "unavailable_dates":
            unavailable.update(rule.values["dates"])
        elif rule.type == "allowed_weekdays":
            allowed = set(rule.values["weekdays"])
            unavailable.update(day for day in dates if day.weekday() not in allowed)
        elif rule.type == "start_date":
            unavailable.update(day for day in dates if day < rule.values["date"])
        elif rule.type == "end_date":
            unavailable.update(day for day in dates if day > rule.values["date"])
    return {day for day in unavailable if day not in doctor.assignments}
