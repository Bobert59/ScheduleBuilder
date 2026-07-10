from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from schedule_builder.errors import HistoryFormatError
from schedule_builder.history import read_history_workbook

from .helpers import make_history_workbook


class HistoryTests(unittest.TestCase):
    def test_imports_every_date_and_separates_open_rows(self) -> None:
        start = date(2026, 1, 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.xlsx"
            make_history_workbook(
                path,
                start,
                4,
                {
                    "Alice": {start: "O/N", start + timedelta(days=1): "O/N"},
                    "OPEN": {start + timedelta(days=2): "8-6"},
                },
            )
            history = read_history_workbook(path, expected_end=date(2026, 1, 4))
        self.assertEqual(len(history.dates), 4)
        self.assertEqual(history.overnight_count("Alice"), 2)
        self.assertEqual(history.open_shifts[start + timedelta(days=2)], ("8-6",))
        self.assertNotIn("OPEN", history.assignments)

    def test_rejects_a_workbook_that_does_not_end_before_new_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.xlsx"
            make_history_workbook(path, date(2026, 1, 1), 2, {"Alice": {}})
            with self.assertRaises(HistoryFormatError):
                read_history_workbook(path, expected_end=date(2026, 1, 3))


if __name__ == "__main__":
    unittest.main()

