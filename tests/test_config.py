from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from schedule_builder.config import load_config
from schedule_builder.domain import DoctorMode
from schedule_builder.errors import ConfigurationError


class ConfigTests(unittest.TestCase):
    def test_modes_assignments_and_rules_are_normalized(self) -> None:
        raw = {
            "schedule": {"start": "2026-10-01", "end": "2026-10-07"},
            "doctors": [
                {
                    "name": "Dr. Example",
                    "mode": "prescribed",
                    "overnight_capable": False,
                    "time_off": [
                        "2026-10-05",
                        {"start": "2026-10-06", "end": "2026-10-7"},
                    ],
                    "assignments": {"2026-10-02": "8-6"},
                    "rules": [
                        {"type": "allowed_weekdays", "weekdays": ["Thu", "Friday"]},
                        {"type": "start_date", "date": "2026-10-01"},
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = load_config(path)
        doctor = config.doctors[0]
        self.assertEqual(doctor.mode, DoctorMode.PRESCRIBED)
        self.assertEqual(
            [day.isoformat() for day in doctor.rules[0].values["dates"]],
            ["2026-10-05", "2026-10-06", "2026-10-07"],
        )
        self.assertEqual(doctor.rules[1].values["weekdays"], [3, 4])
        self.assertEqual(next(iter(doctor.assignments.values())), "8-6")

    def test_default_doctor_cannot_have_prescribed_assignments(self) -> None:
        raw = {
            "schedule": {"start": "2026-10-01", "end": "2026-10-07"},
            "doctors": [
                {
                    "name": "Doctor",
                    "mode": "default",
                    "assignments": {"2026-10-02": "8-6"},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_config(path)

    def test_time_off_range_cannot_end_before_it_starts(self) -> None:
        raw = {
            "schedule": {"start": "2026-10-01", "end": "2026-10-07"},
            "doctors": [
                {
                    "name": "Doctor",
                    "time_off": [{"start": "2026-10-06", "end": "2026-10-04"}],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_config(path)

    def test_legacy_weekend_pair_limits_are_migrated_to_shift_limits(self) -> None:
        raw = {
            "schedule": {"start": "2026-10-01", "end": "2026-10-07"},
            "default_rules": {"max_weekend_pairs": 2},
            "doctors": [{"name": "Dr. Legacy", "max_weekend_pairs": 1}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = load_config(path)
        self.assertEqual(config.default_rules.max_weekend_shifts, 4)
        self.assertEqual(config.doctors[0].max_weekend_shifts, 2)


if __name__ == "__main__":
    unittest.main()
