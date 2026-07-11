from __future__ import annotations

import unittest
import threading
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from schedule_builder.domain import (
    DefaultRules,
    DoctorConfig,
    DoctorMode,
    HistorySchedule,
    RuleSpec,
    ScheduleConfig,
    SolverSettings,
)
from schedule_builder.errors import ScheduleCancelledError
from schedule_builder.optimizer import ScheduleOptimizer


def empty_history(end: date, days: int = 7) -> HistorySchedule:
    dates = tuple(end - timedelta(days=offset) for offset in reversed(range(days)))
    return HistorySchedule(source=Path("test.xlsx"), dates=dates, assignments={}, open_shifts={})


def fast_config(start: date, end: date, doctors: tuple[DoctorConfig, ...]) -> ScheduleConfig:
    return ScheduleConfig(
        start=start,
        end=end,
        doctors=doctors,
        default_rules=DefaultRules(
            max_consecutive_days=7,
            rolling_window_days=7,
            max_shifts_in_rolling_window=7,
            max_overnights=7,
            max_weekend_shifts=8,
        ),
        solver=SolverSettings(max_time_per_phase_seconds=3, workers=4, random_seed=7),
    )


class OptimizerTests(unittest.TestCase):
    def test_generation_can_be_cancelled_before_solving(self) -> None:
        start = date(2026, 10, 5)
        cancel_event = threading.Event()
        cancel_event.set()
        optimizer = ScheduleOptimizer(
            fast_config(start, start, (DoctorConfig(name="Doctor"),)),
            empty_history(start - timedelta(days=1)),
            cancel_event=cancel_event,
        )
        with self.assertRaises(ScheduleCancelledError):
            optimizer.solve()

    def test_fixed_and_prescribed_assignments_are_immutable(self) -> None:
        start = date(2026, 10, 5)
        doctors = (
            DoctorConfig(
                name="Fixed",
                mode=DoctorMode.FIXED,
                assignments={start: "8-6"},
            ),
            DoctorConfig(
                name="Prescribed",
                mode=DoctorMode.PRESCRIBED,
                assignments={start: "8-8"},
                rules=(RuleSpec("allowed_weekdays", {"weekdays": [1]}),),
            ),
        )
        result = ScheduleOptimizer(fast_config(start, start, doctors), empty_history(start - timedelta(days=1))).solve()
        self.assertEqual(result.assignments["Fixed"][start], "8-6")
        self.assertEqual(result.assignments["Prescribed"][start], "8-8")

    def test_open_priority_leaves_weekday_86_open_before_212(self) -> None:
        start = date(2026, 10, 5)  # Monday
        doctor = DoctorConfig(
            name="Only Doctor",
            rules=(RuleSpec("max_total_shifts", {"value": 1}),),
        )
        result = ScheduleOptimizer(fast_config(start, start, (doctor,)), empty_history(start - timedelta(days=1))).solve()
        self.assertEqual(result.assignments["Only Doctor"][start], "2-12")
        self.assertEqual(result.open_shifts[start], ("8-6", "8-8", "O/N"))

    def test_weekend_pairing_is_soft_not_hard(self) -> None:
        saturday = date(2026, 10, 3)
        doctor = DoctorConfig(
            name="Saturday Doctor",
            rules=(
                RuleSpec("allowed_weekdays", {"weekdays": [5]}),
                RuleSpec("allowed_shifts", {"shifts": ["8-6"]}),
                RuleSpec("max_total_shifts", {"value": 1}),
            ),
        )
        result = ScheduleOptimizer(
            fast_config(saturday, saturday + timedelta(days=1), (doctor,)),
            empty_history(saturday - timedelta(days=1)),
        ).solve()
        self.assertEqual(result.assignments["Saturday Doctor"][saturday], "8-6")
        self.assertNotIn(saturday + timedelta(days=1), result.assignments["Saturday Doctor"])

    def test_max_weekend_shifts_remains_a_hard_cap(self) -> None:
        start = date(2026, 10, 5)  # Monday; the window contains two full weekends.
        doctor = DoctorConfig(
            name="Weekend Doctor",
            max_weekend_shifts=2,
            rules=(
                RuleSpec("allowed_weekdays", {"weekdays": [5, 6]}),
                RuleSpec("allowed_shifts", {"shifts": ["8-6"]}),
                RuleSpec("max_total_shifts", {"value": 4}),
            ),
        )
        result = ScheduleOptimizer(
            fast_config(start, start + timedelta(days=13), (doctor,)),
            empty_history(start - timedelta(days=1)),
        ).solve()
        assignments = result.assignments[doctor.name]
        weekend_shift_count = sum(day.weekday() >= 5 for day in assignments)
        self.assertLessEqual(weekend_shift_count, 2)

    def test_overnights_use_blocks_and_balance_against_history(self) -> None:
        start = date(2026, 10, 5)
        end = start + timedelta(days=13)
        overnight_only = (RuleSpec("allowed_shifts", {"shifts": ["O/N"]}),)
        doctors = tuple(
            DoctorConfig(
                name=name,
                overnight_capable=True,
                rules=overnight_only,
                max_weekend_shifts=8,
            )
            for name in ("A", "B", "C")
        )
        history = empty_history(start - timedelta(days=1))
        first_history_day = history.dates[0]
        history = HistorySchedule(
            source=history.source,
            dates=history.dates,
            assignments={
                "A": {
                    first_history_day: "O/N",
                    first_history_day + timedelta(days=1): "O/N",
                }
            },
            open_shifts={},
        )
        result = ScheduleOptimizer(fast_config(start, end, doctors), history).solve()
        self.assertTrue(all("O/N" not in shifts for shifts in result.open_shifts.values()))

        combined_counts = []
        for doctor in doctors:
            visible_days = sorted(
                day for day, shift in result.assignments[doctor.name].items() if shift == "O/N"
            )
            combined_counts.append(result.history_overnights[doctor.name] + len(visible_days))
            blocks: list[int] = []
            for day in visible_days:
                if day - timedelta(days=1) not in visible_days:
                    length = 1
                    while day + timedelta(days=length) in visible_days:
                        length += 1
                    blocks.append(length)
            self.assertNotIn(1, blocks)
            self.assertTrue(all(length in (2, 3) for length in blocks))
        self.assertLessEqual(max(combined_counts) - min(combined_counts), 1)

    def test_doctor_specific_overnight_block_limit_spans_history_boundary(self) -> None:
        start = date(2026, 10, 5)
        overnight_only = RuleSpec("allowed_shifts", {"shifts": ["O/N"]})
        doctors = (
            DoctorConfig(
                name="Dr. Example",
                overnight_capable=True,
                rules=(
                    overnight_only,
                    RuleSpec("max_overnight_block_length", {"value": 2}),
                ),
            ),
            DoctorConfig(name="Other", overnight_capable=True, rules=(overnight_only,)),
        )
        history_dates = tuple(start - timedelta(days=offset) for offset in reversed(range(7)))
        history = HistorySchedule(
            source=Path("test.xlsx"),
            dates=history_dates,
            assignments={
                "Dr. Example": {
                    history_dates[-2]: "O/N",
                    history_dates[-1]: "O/N",
                }
            },
            open_shifts={},
        )
        result = ScheduleOptimizer(fast_config(start, start, doctors), history).solve()
        self.assertNotIn(start, result.assignments["Dr. Example"])
        self.assertEqual(result.assignments["Other"][start], "O/N")

    def test_88_shifts_prefer_two_day_blocks(self) -> None:
        start = date(2026, 10, 5)  # Monday
        doctors = tuple(
            DoctorConfig(
                name=name,
                target_hours=24,
                rules=(RuleSpec("allowed_shifts", {"shifts": ["8-8"]}),),
            )
            for name in ("A", "B")
        )
        result = ScheduleOptimizer(
            fast_config(start, start + timedelta(days=3), doctors),
            empty_history(start - timedelta(days=1)),
        ).solve()
        for doctor in doctors:
            days = sorted(result.assignments[doctor.name])
            self.assertEqual(len(days), 2)
            self.assertEqual(days[1] - days[0], timedelta(days=1))

    def test_88_pair_preference_uses_history_boundary(self) -> None:
        start = date(2026, 10, 5)
        rule = (RuleSpec("allowed_shifts", {"shifts": ["8-8"]}),)
        doctors = (
            DoctorConfig(name="History Pair", target_hours=12, rules=rule),
            DoctorConfig(name="New Singleton", target_hours=12, rules=rule),
        )
        history = empty_history(start - timedelta(days=1))
        history = HistorySchedule(
            source=history.source,
            dates=history.dates,
            assignments={"History Pair": {history.dates[-1]: "8-8"}},
            open_shifts={},
        )
        result = ScheduleOptimizer(fast_config(start, start, doctors), history).solve()
        self.assertEqual(result.assignments["History Pair"][start], "8-8")
        self.assertNotIn(start, result.assignments["New Singleton"])

    def test_three_day_88_block_is_allowed(self) -> None:
        start = date(2026, 10, 5)
        doctor = DoctorConfig(
            name="Three Day Doctor",
            target_hours=36,
            rules=(RuleSpec("allowed_shifts", {"shifts": ["8-8"]}),),
        )
        result = ScheduleOptimizer(
            fast_config(start, start + timedelta(days=2), (doctor,)),
            empty_history(start - timedelta(days=1)),
        ).solve()
        self.assertEqual(len(result.assignments[doctor.name]), 3)
        self.assertTrue(
            all(shift == "8-8" for shift in result.assignments[doctor.name].values())
        )

    def test_overnight_is_forbidden_before_vacation(self) -> None:
        start = date(2026, 10, 5)
        vacation_day = start + timedelta(days=1)
        doctors = (
            DoctorConfig(
                name="Vacation Doctor",
                overnight_capable=True,
                rules=(RuleSpec("unavailable_dates", {"dates": [vacation_day]}),),
            ),
            DoctorConfig(name="Covering Doctor", overnight_capable=True),
        )
        result = ScheduleOptimizer(
            fast_config(start, vacation_day, doctors),
            empty_history(start - timedelta(days=1)),
        ).solve()
        self.assertNotEqual(result.assignments["Vacation Doctor"].get(start), "O/N")

    def test_max_consecutive_days_spans_history_boundary(self) -> None:
        start = date(2026, 9, 21)
        doctor = DoctorConfig(name="Boundary Doctor")
        history = empty_history(start - timedelta(days=1))
        history = HistorySchedule(
            source=history.source,
            dates=history.dates,
            assignments={
                doctor.name: {
                    history.dates[-2]: "8-6",
                    history.dates[-1]: "8-6",
                }
            },
            open_shifts={},
        )
        config = fast_config(start, start + timedelta(days=1), (doctor,))
        config = replace(
            config,
            default_rules=replace(config.default_rules, max_consecutive_days=3),
        )
        result = ScheduleOptimizer(config, history).solve()
        self.assertLessEqual(len(result.assignments[doctor.name]), 1)


if __name__ == "__main__":
    unittest.main()
