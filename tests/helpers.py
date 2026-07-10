from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook


def make_history_workbook(
    path: Path,
    start: date,
    days: int,
    rows: dict[str, dict[date, str]],
) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Schedule"
    sheet.cell(1, 1, "Doctor")
    dates = [start + timedelta(days=offset) for offset in range(days)]
    for column, day in enumerate(dates, start=2):
        sheet.cell(1, column, f"{day:%a}\n{day:%b %d}")
    for row, (name, assignments) in enumerate(rows.items(), start=2):
        sheet.cell(row, 1, name)
        for column, day in enumerate(dates, start=2):
            sheet.cell(row, column, assignments.get(day))
    workbook.save(path)
    return path

