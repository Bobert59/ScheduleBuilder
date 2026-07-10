from __future__ import annotations

import unittest
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
            max_weekend_pairs=4,
        ),
        solver=SolverSettings(max_time_per_phase_seconds=3, workers=4, random_seed=7),
    )


class OptimizerTests(unittest.TestCase):
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

    def test_overnights_use_blocks_and_balance_against_history(self) -> None:
        start = date(2026, 10, 5)
        end = start + timedelta(days=13)
        overnight_only = (RuleSpec("allowed_shifts", {"shifts": ["O/N"]}),)
        doctors = tuple(
            DoctorConfig(
                name=name,
                overnight_capable=True,
                rules=overnight_only,
                max_weekend_pairs=4,
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


if __name__ == "__main__":
    unittest.main()
