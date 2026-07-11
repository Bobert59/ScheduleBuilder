from __future__ import annotations

import itertools
import threading
from datetime import date

from ortools.sat.python import cp_model

from .domain import (
    DAY_SHIFTS,
    OVERNIGHT,
    SHIFT_HOURS,
    SHIFT_NAMES,
    DoctorMode,
    HistorySchedule,
    PhaseReport,
    RuleSpec,
    ScheduleConfig,
    ScheduleResult,
)
from .errors import ScheduleCancelledError, ScheduleInfeasibleError
from .rules import RuleContext, apply_rule, unavailable_dates_for


def _lexicographic_expression(items: list[tuple[cp_model.LinearExpr | int, int]]) -> cp_model.LinearExpr:
    """Encode bounded, non-negative terms as an exact lexicographic objective."""
    expression: cp_model.LinearExpr | int = 0
    weight = 1
    for term, upper_bound in reversed(items):
        expression += term * weight
        weight *= upper_bound + 1
        if weight >= 9_000_000_000_000_000_000:
            raise ValueError("Lexicographic objective exceeds the CP-SAT integer range.")
    return expression


class ScheduleOptimizer:
    def __init__(
        self,
        config: ScheduleConfig,
        history: HistorySchedule,
        cancel_event: threading.Event | None = None,
    ):
        self.config = config
        self.history = history
        self.dates = config.dates
        self.doctors = config.doctors
        self.shift_index = {shift: i for i, shift in enumerate(SHIFT_NAMES)}
        self.model = cp_model.CpModel()
        self.x: dict[tuple[int, int, int], cp_model.IntVar] = {}
        self.work: dict[tuple[int, int], cp_model.IntVar] = {}
        self.open: dict[tuple[int, int], cp_model.IntVar] = {}
        self.protected: dict[int, set[int]] = {}
        self.overnight_singletons: list[cp_model.IntVar] = []
        self.overnight_triples: list[cp_model.IntVar] = []
        self.overnight_weekend_breaks: list[cp_model.IntVar] = []
        self.overnight_totals: dict[str, cp_model.IntVar] = {}
        self.weekend_singles: list[cp_model.IntVar] = []
        self.isolated_days: list[cp_model.IntVar] = []
        self.shift_88_singletons: list[cp_model.IntVar] = []
        self.shift_88_triples: list[cp_model.IntVar] = []
        self.hour_squares: list[cp_model.IntVar] = []
        self.target_hours = self._calculate_target_hours()
        self.phase_reports: list[PhaseReport] = []
        self.cancel_event = cancel_event

    def solve(self) -> ScheduleResult:
        self._build_variables_and_coverage()
        self._lock_user_assignments()
        self._apply_doctor_rules()
        self._apply_vacation_overnight_rules()
        self._apply_history_boundary_work_rules()
        self._apply_transition_rules()
        self._build_overnight_rules()
        self._build_weekend_rules()
        self._build_quality_terms()

        # Phase 1: coverage of O/N, avoidance of singleton nights, then historical balance.
        capable_count = sum(doctor.overnight_capable for doctor in self.doctors)
        history_and_window = len(self.history.dates) + len(self.dates)
        pair_count = capable_count * max(0, capable_count - 1) // 2
        open_overnights = sum(self.open[d, self.shift_index[OVERNIGHT]] for d in range(len(self.dates)))
        singleton_count = sum(self.overnight_singletons)
        triple_count = sum(self.overnight_triples)
        spread, pairwise_difference = self._overnight_balance_terms(history_and_window)
        overnight_objective = _lexicographic_expression(
            [
                (open_overnights, len(self.dates)),
                (singleton_count, len(self.doctors) * len(self.dates)),
                (spread, history_and_window),
                (
                    sum(self.overnight_weekend_breaks),
                    len(self.doctors) * max(1, len(self.dates) // 7),
                ),
                (pairwise_difference, pair_count * history_and_window),
                (triple_count, len(self.doctors) * len(self.dates)),
            ]
        )
        overnight_solver = self._run_phase("Overnights", overnight_objective)
        overnight_index = self.shift_index[OVERNIGHT]
        for k in range(len(self.doctors)):
            for d in range(len(self.dates)):
                self.model.add(self.x[k, d, overnight_index] == overnight_solver.boolean_value(self.x[k, d, overnight_index]))
        for d in range(len(self.dates)):
            self.model.add(self.open[d, overnight_index] == overnight_solver.boolean_value(self.open[d, overnight_index]))

        # Phase 2: trade inexpensive weekday OPEN shifts against split-weekend penalties.
        coverage_objective = self._coverage_and_weekend_objective()
        coverage_solver = self._run_phase("Coverage and weekends", coverage_objective)
        self.model.add(coverage_objective == coverage_solver.value(coverage_objective))
        if self.weekend_singles:
            weekend_count = sum(self.weekend_singles)
            self.model.add(weekend_count == coverage_solver.value(weekend_count))

        # Phase 3: preserve all earlier decisions while improving the human-facing roster.
        weights = self.config.quality_weights
        quality_objective = (
            weights.hour_balance * sum(self.hour_squares)
            + weights.weekend_single * sum(self.weekend_singles)
            + weights.isolated_workday * sum(self.isolated_days)
            + weights.shift_88_singleton * sum(self.shift_88_singletons)
            + weights.shift_88_triple * sum(self.shift_88_triples)
        )
        final_solver = self._run_phase("Schedule quality", quality_objective)
        return self._extract_result(final_solver)

    def _build_variables_and_coverage(self) -> None:
        for k, doctor in enumerate(self.doctors):
            self.protected[k] = {
                d for d, day in enumerate(self.dates) if doctor.is_protected(day)
            }
            for d in range(len(self.dates)):
                self.work[k, d] = self.model.new_bool_var(f"work_{k}_{d}")
                for s in range(len(SHIFT_NAMES)):
                    self.x[k, d, s] = self.model.new_bool_var(f"x_{k}_{d}_{s}")
                self.model.add(
                    self.work[k, d] == sum(self.x[k, d, s] for s in range(len(SHIFT_NAMES)))
                )
        for d in range(len(self.dates)):
            for s in range(len(SHIFT_NAMES)):
                self.open[d, s] = self.model.new_bool_var(f"open_{d}_{s}")
                self.model.add(
                    sum(self.x[k, d, s] for k in range(len(self.doctors))) + self.open[d, s] == 1
                )

    def _lock_user_assignments(self) -> None:
        for k, doctor in enumerate(self.doctors):
            if not doctor.overnight_capable:
                overnight = self.shift_index[OVERNIGHT]
                for d in range(len(self.dates)):
                    self.model.add(self.x[k, d, overnight] == 0)
            if doctor.mode == DoctorMode.FIXED:
                for d, day in enumerate(self.dates):
                    desired = doctor.assignments.get(day)
                    for s, shift in enumerate(SHIFT_NAMES):
                        self.model.add(self.x[k, d, s] == int(shift == desired))
            elif doctor.mode == DoctorMode.PRESCRIBED:
                for day, shift in doctor.assignments.items():
                    d = self.dates.index(day)
                    self.model.add(self.x[k, d, self.shift_index[shift]] == 1)

    def _apply_doctor_rules(self) -> None:
        defaults = self.config.default_rules
        for k, doctor in enumerate(self.doctors):
            if doctor.mode == DoctorMode.FIXED:
                continue
            context = RuleContext(
                model=self.model,
                dates=self.dates,
                doctor=doctor,
                shift_index=self.shift_index,
                assignments={(d, s): self.x[k, d, s] for d in range(len(self.dates)) for s in range(len(SHIFT_NAMES))},
                work={d: self.work[k, d] for d in range(len(self.dates))},
                protected_days=self.protected[k],
            )
            if doctor.use_default_rest_rules:
                apply_rule(
                    context,
                    RuleSpec("max_consecutive_days", {"value": defaults.max_consecutive_days}),
                )
                apply_rule(
                    context,
                    RuleSpec(
                        "rolling_limit",
                        {
                            "window_days": defaults.rolling_window_days,
                            "max_shifts": defaults.max_shifts_in_rolling_window,
                        },
                    ),
                )
                apply_rule(context, RuleSpec("max_overnights", {"value": defaults.max_overnights}))
            for rule in doctor.rules:
                apply_rule(context, rule)

    def _apply_transition_rules(self) -> None:
        idx_86 = self.shift_index["8-6"]
        idx_88 = self.shift_index["8-8"]
        idx_212 = self.shift_index["2-12"]
        defaults = self.config.default_rules
        for k, doctor in enumerate(self.doctors):
            if doctor.mode == DoctorMode.FIXED:
                continue
            if defaults.forbid_86_after_88:
                for d in range(len(self.dates) - 1):
                    if d in self.protected[k] or d + 1 in self.protected[k]:
                        continue
                    self.model.add(self.x[k, d, idx_88] + self.x[k, d + 1, idx_86] <= 1)

            for d in range(len(self.dates)):
                end_212 = self.model.new_bool_var(f"end212_{k}_{d}")
                current = self.x[k, d, idx_212]
                if d == len(self.dates) - 1:
                    self.model.add(end_212 == current)
                else:
                    following = self.x[k, d + 1, idx_212]
                    self.model.add(end_212 <= current)
                    self.model.add(end_212 + following <= 1)
                    self.model.add(end_212 >= current - following)
                for gap in range(1, defaults.recovery_after_212_days + 1):
                    future = d + gap
                    if future >= len(self.dates) or future in self.protected[k]:
                        continue
                    self.model.add(
                        sum(self.x[k, future, self.shift_index[shift]] for shift in DAY_SHIFTS) == 0
                    ).only_enforce_if(end_212)

    def _apply_vacation_overnight_rules(self) -> None:
        """An O/N shift cannot run into the first day of explicit time off."""
        overnight = self.shift_index[OVERNIGHT]
        for k, doctor in enumerate(self.doctors):
            unavailable = {
                day
                for rule in doctor.rules
                if rule.type == "unavailable_dates"
                for day in rule.values["dates"]
            }
            for d in range(len(self.dates) - 1):
                if self.dates[d + 1] in unavailable:
                    self.model.add(self.x[k, d, overnight] == 0)

    def _apply_history_boundary_work_rules(self) -> None:
        """Extend consecutive-day and rolling limits across history into day one."""
        history_length = len(self.history.dates)
        if history_length == 0:
            return
        defaults = self.config.default_rules
        for k, doctor in enumerate(self.doctors):
            if doctor.mode == DoctorMode.FIXED:
                continue
            history_work = [
                self.model.new_constant(int(self.history.shift_for(doctor.name, day) is not None))
                for day in self.history.dates
            ]
            sequence = history_work + [self.work[k, d] for d in range(len(self.dates))]
            limits: list[tuple[int, int]] = []
            if doctor.use_default_rest_rules:
                limits.append((defaults.max_consecutive_days + 1, defaults.max_consecutive_days))
                limits.append((defaults.rolling_window_days, defaults.max_shifts_in_rolling_window))
            for rule in doctor.rules:
                if rule.type == "max_consecutive_days":
                    maximum = int(rule.values["value"])
                    limits.append((maximum + 1, maximum))
                elif rule.type == "rolling_limit":
                    limits.append(
                        (int(rule.values["window_days"]), int(rule.values["max_shifts"]))
                    )

            for width, maximum in limits:
                first_start = max(0, history_length - width + 1)
                for start in range(first_start, history_length):
                    end = start + width
                    if end <= history_length or end > len(sequence):
                        continue
                    current_indices = range(0, end - history_length)
                    protected = sum(d in self.protected[k] for d in current_indices)
                    self.model.add(sum(sequence[start:end]) <= maximum + protected)

    def _build_overnight_rules(self) -> None:
        overnight = self.shift_index[OVERNIGHT]
        history_dates = self.history.dates
        history_length = len(history_dates)
        for k, doctor in enumerate(self.doctors):
            historical_flags = [
                self.model.new_constant(int(self.history.shift_for(doctor.name, day) == OVERNIGHT))
                for day in history_dates
            ]
            current_flags = [self.x[k, d, overnight] for d in range(len(self.dates))]
            sequence = historical_flags + current_flags
            historical_count = self.history.overnight_count(doctor.name)
            total = self.model.new_int_var(
                historical_count,
                historical_count + len(self.dates),
                f"combined_overnights_{k}",
            )
            self.model.add(total == historical_count + sum(current_flags))
            if doctor.overnight_capable:
                self.overnight_totals[doctor.name] = total
            if not doctor.overnight_capable or doctor.mode == DoctorMode.FIXED:
                continue

            for saturday, day in enumerate(self.dates):
                if day.weekday() != 5 or saturday + 1 >= len(self.dates):
                    continue
                sunday = saturday + 1
                if saturday in self.protected[k] or sunday in self.protected[k]:
                    continue
                saturday_end = self.model.new_bool_var(
                    f"overnight_weekend_break_{k}_{saturday}"
                )
                self.model.add(saturday_end <= current_flags[saturday])
                self.model.add(saturday_end + current_flags[sunday] <= 1)
                self.model.add(
                    saturday_end >= current_flags[saturday] - current_flags[sunday]
                )
                self.overnight_weekend_breaks.append(saturday_end)

            maximum_block_length = min(
                (
                    int(rule.values["value"])
                    for rule in doctor.rules
                    if rule.type == "max_overnight_block_length"
                ),
                default=3,
            )
            block_window = maximum_block_length + 1
            for start in range(len(sequence) - block_window + 1):
                current_indices = [
                    i - history_length
                    for i in range(start, start + block_window)
                    if i >= history_length
                ]
                protected = sum(d in self.protected[k] for d in current_indices)
                self.model.add(
                    sum(sequence[start : start + block_window])
                    <= maximum_block_length + protected
                )

            for i in range(len(sequence)):
                current = sequence[i]
                end = self.model.new_bool_var(f"overnight_end_{k}_{i}")
                if i == len(sequence) - 1:
                    self.model.add(end == current)
                else:
                    following = sequence[i + 1]
                    self.model.add(end <= current)
                    self.model.add(end + following <= 1)
                    self.model.add(end >= current - following)

                previous = sequence[i - 1] if i >= 1 else self.model.new_constant(0)
                single = self.model.new_bool_var(f"overnight_single_{k}_{i}")
                self.model.add(single <= end)
                self.model.add(single + previous <= 1)
                self.model.add(single >= end - previous)

                previous_two = sequence[i - 2] if i >= 2 else self.model.new_constant(0)
                triple = self.model.new_bool_var(f"overnight_triple_{k}_{i}")
                self.model.add(triple <= end)
                self.model.add(triple <= previous)
                self.model.add(triple <= previous_two)
                self.model.add(triple >= end + previous + previous_two - 2)

                current_day_index = i - history_length
                if current_day_index >= 0 and current_day_index not in self.protected[k]:
                    self.overnight_singletons.append(single)
                    self.overnight_triples.append(triple)

                for gap in (1, 2, 3):
                    seq_future = i + gap
                    schedule_future = seq_future - history_length
                    if (
                        0 <= schedule_future < len(self.dates)
                        and schedule_future not in self.protected[k]
                    ):
                        self.model.add(self.work[k, schedule_future] == 0).only_enforce_if(end)
                seq_future = i + 4
                schedule_future = seq_future - history_length
                if (
                    0 <= schedule_future < len(self.dates)
                    and schedule_future not in self.protected[k]
                ):
                    self.model.add(self.work[k, schedule_future] == 0).only_enforce_if(triple)

    def _overnight_balance_terms(self, upper_bound: int) -> tuple[cp_model.LinearExpr | int, cp_model.LinearExpr | int]:
        totals = list(self.overnight_totals.values())
        if len(totals) < 2:
            return 0, 0
        maximum = self.model.new_int_var(0, upper_bound, "maximum_combined_overnights")
        minimum = self.model.new_int_var(0, upper_bound, "minimum_combined_overnights")
        self.model.add_max_equality(maximum, totals)
        self.model.add_min_equality(minimum, totals)
        spread = maximum - minimum
        differences: list[cp_model.IntVar] = []
        for number, (left, right) in enumerate(itertools.combinations(totals, 2)):
            difference = self.model.new_int_var(0, upper_bound, f"overnight_pair_difference_{number}")
            self.model.add_abs_equality(difference, left - right)
            differences.append(difference)
        return spread, sum(differences)

    def _build_weekend_rules(self) -> None:
        for k, doctor in enumerate(self.doctors):
            if doctor.mode == DoctorMode.FIXED:
                continue
            counted_weekend_days: list[cp_model.IntVar] = []
            for saturday, day in enumerate(self.dates):
                if day.weekday() != 5 or saturday + 1 >= len(self.dates):
                    continue
                sunday = saturday + 1
                pair = self.model.new_bool_var(f"weekend_pair_{k}_{saturday}")
                self.model.add(pair <= self.work[k, saturday])
                self.model.add(pair <= self.work[k, sunday])
                self.model.add(pair >= self.work[k, saturday] + self.work[k, sunday] - 1)
                if saturday not in self.protected[k]:
                    counted_weekend_days.append(self.work[k, saturday])
                if sunday not in self.protected[k]:
                    counted_weekend_days.append(self.work[k, sunday])
                if saturday not in self.protected[k] and sunday not in self.protected[k]:
                    split = self.model.new_bool_var(f"weekend_single_{k}_{saturday}")
                    self.model.add_abs_equality(split, self.work[k, saturday] - self.work[k, sunday])
                    self.weekend_singles.append(split)
            maximum = (
                doctor.max_weekend_shifts
                if doctor.max_weekend_shifts is not None
                else self.config.default_rules.max_weekend_shifts
            )
            self.model.add(sum(counted_weekend_days) <= maximum)

    def _build_quality_terms(self) -> None:
        self._build_88_block_quality_terms()
        for k, doctor in enumerate(self.doctors):
            if doctor.mode != DoctorMode.FIXED:
                max_hours = len(self.dates) * max(SHIFT_HOURS.values())
                hours = self.model.new_int_var(0, max_hours, f"hours_{k}")
                self.model.add(
                    hours
                    == sum(
                        SHIFT_HOURS[shift] * self.x[k, d, self.shift_index[shift]]
                        for d in range(len(self.dates))
                        for shift in SHIFT_NAMES
                    )
                )
                difference_bound = max(max_hours, self.target_hours[doctor.name])
                difference = self.model.new_int_var(-difference_bound, difference_bound, f"hour_difference_{k}")
                self.model.add(difference == hours - self.target_hours[doctor.name])
                square = self.model.new_int_var(0, difference_bound**2, f"hour_square_{k}")
                self.model.add_multiplication_equality(square, [difference, difference])
                self.hour_squares.append(square)

            if doctor.mode == DoctorMode.FIXED:
                continue
            previous_history_work = int(
                bool(self.history.dates)
                and self.history.shift_for(doctor.name, self.history.dates[-1]) is not None
            )
            sequence = [self.model.new_constant(previous_history_work)] + [
                self.work[k, d] for d in range(len(self.dates))
            ]
            for d in range(len(self.dates) - 1):
                if d in self.protected[k]:
                    continue
                previous = sequence[d]
                current = sequence[d + 1]
                following = sequence[d + 2]
                isolated = self.model.new_bool_var(f"isolated_workday_{k}_{d}")
                self.model.add(isolated <= current)
                self.model.add(isolated + previous <= 1)
                self.model.add(isolated + following <= 1)
                self.model.add(isolated >= current - previous - following)
                self.isolated_days.append(isolated)

    def _build_88_block_quality_terms(self) -> None:
        """Prefer two-day 8-8 blocks, including blocks crossing from history."""
        shift_88 = self.shift_index["8-8"]
        history_length = len(self.history.dates)
        for k, doctor in enumerate(self.doctors):
            if doctor.mode == DoctorMode.FIXED:
                continue
            historical_flags = [
                self.model.new_constant(
                    int(self.history.shift_for(doctor.name, day) == "8-8")
                )
                for day in self.history.dates
            ]
            sequence = historical_flags + [
                self.x[k, d, shift_88] for d in range(len(self.dates))
            ]
            for i in range(history_length, len(sequence)):
                current_day = i - history_length
                if current_day in self.protected[k]:
                    continue

                current = sequence[i]
                end = self.model.new_bool_var(f"shift_88_end_{k}_{i}")
                if i == len(sequence) - 1:
                    self.model.add(end == current)
                else:
                    following = sequence[i + 1]
                    self.model.add(end <= current)
                    self.model.add(end + following <= 1)
                    self.model.add(end >= current - following)

                previous = sequence[i - 1] if i >= 1 else self.model.new_constant(0)
                singleton = self.model.new_bool_var(f"shift_88_singleton_{k}_{i}")
                self.model.add(singleton <= end)
                self.model.add(singleton + previous <= 1)
                self.model.add(singleton >= end - previous)
                self.shift_88_singletons.append(singleton)

                previous_two = sequence[i - 2] if i >= 2 else self.model.new_constant(0)
                triple = self.model.new_bool_var(f"shift_88_triple_{k}_{i}")
                self.model.add(triple <= end)
                self.model.add(triple <= previous)
                self.model.add(triple <= previous_two)
                self.model.add(triple >= end + previous + previous_two - 2)
                self.shift_88_triples.append(triple)

    def _coverage_and_weekend_objective(self) -> cp_model.LinearExpr:
        split_cost = max(1, int(self.config.quality_weights.weekend_single))
        weekday_costs = {
            "8-6": 1_000,
            "8-8": 1_500,
            "2-12": 2_000,
        }
        weekend_costs = {
            "8-6": split_cost * 3,
            "8-8": split_cost * 4,
            "2-12": split_cost * 5,
        }
        open_cost: cp_model.LinearExpr | int = 0
        for d, day in enumerate(self.dates):
            costs = weekend_costs if day.weekday() >= 5 else weekday_costs
            for shift in DAY_SHIFTS:
                open_cost += costs[shift] * self.open[d, self.shift_index[shift]]
        # OPEN O/N was already minimized and frozen in phase 1.
        return open_cost + split_cost * sum(self.weekend_singles)

    def _calculate_target_hours(self) -> dict[str, int]:
        targets: dict[str, int] = {}
        total_days = len(self.dates)
        for doctor in self.doctors:
            if doctor.target_hours is not None:
                targets[doctor.name] = doctor.target_hours
                continue
            if doctor.mode == DoctorMode.FIXED:
                targets[doctor.name] = sum(SHIFT_HOURS[shift] for shift in doctor.assignments.values())
                continue
            unavailable = len(unavailable_dates_for(doctor, self.dates))
            if unavailable >= self.config.prorate_after_unavailable_days:
                targets[doctor.name] = round(
                    self.config.base_target_hours * (total_days - unavailable) / total_days
                )
            else:
                targets[doctor.name] = self.config.base_target_hours
        return targets

    def _run_phase(self, name: str, objective: cp_model.LinearExpr | int) -> cp_model.CpSolver:
        self._raise_if_cancelled()
        self.model.clear_objective()
        self.model.minimize(objective)
        solver = cp_model.CpSolver()
        settings = self.config.solver
        solver.parameters.max_time_in_seconds = settings.max_time_per_phase_seconds
        solver.parameters.num_search_workers = settings.workers
        solver.parameters.random_seed = settings.random_seed
        solver.parameters.log_search_progress = settings.log_progress
        phase_finished = threading.Event()
        monitor: threading.Thread | None = None
        if self.cancel_event is not None:
            monitor = threading.Thread(
                target=self._monitor_cancellation,
                args=(solver, phase_finished),
                daemon=True,
            )
            monitor.start()
        try:
            status = solver.solve(self.model)
        finally:
            phase_finished.set()
            if monitor is not None:
                monitor.join(timeout=0.2)
        self._raise_if_cancelled()
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise ScheduleInfeasibleError(
                f"{name} phase failed with status {solver.status_name(status)}. "
                "Check fixed/prescribed assignments and doctor availability."
            )
        self.phase_reports.append(
            PhaseReport(
                name=name,
                status=solver.status_name(status),
                objective=solver.objective_value,
                wall_time_seconds=solver.wall_time,
            )
        )
        return solver

    def _monitor_cancellation(
        self,
        solver: cp_model.CpSolver,
        phase_finished: threading.Event,
    ) -> None:
        while not phase_finished.wait(0.05):
            if self.cancel_event is not None and self.cancel_event.is_set():
                solver.stop_search()
                return

    def _raise_if_cancelled(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise ScheduleCancelledError("Schedule generation was stopped.")

    def _extract_result(self, solver: cp_model.CpSolver) -> ScheduleResult:
        assignments: dict[str, dict[date, str]] = {}
        for k, doctor in enumerate(self.doctors):
            roster: dict[date, str] = {}
            for d, day in enumerate(self.dates):
                for s, shift in enumerate(SHIFT_NAMES):
                    if solver.boolean_value(self.x[k, d, s]):
                        roster[day] = shift
                        break
            assignments[doctor.name] = roster
        open_shifts: dict[date, tuple[str, ...]] = {}
        for d, day in enumerate(self.dates):
            shifts = tuple(
                shift
                for s, shift in enumerate(SHIFT_NAMES)
                if solver.boolean_value(self.open[d, s])
            )
            if shifts:
                open_shifts[day] = shifts
        return ScheduleResult(
            assignments=assignments,
            open_shifts=open_shifts,
            target_hours=self.target_hours,
            history_overnights={
                doctor.name: self.history.overnight_count(doctor.name) for doctor in self.doctors
            },
            phase_reports=tuple(self.phase_reports),
        )
