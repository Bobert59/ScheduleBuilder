from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .domain import (
    SHIFT_HOURS,
    SHIFT_NAMES,
    DoctorMode,
    HistorySchedule,
    ScheduleConfig,
    ScheduleResult,
)
from .rules import unavailable_dates_for


HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
WEEKEND_FILL = PatternFill("solid", fgColor="548235")
UNAVAILABLE_FILL = PatternFill("solid", fgColor="F4CCCC")
FIXED_FILL = PatternFill("solid", fgColor="CFE2F3")
PRESCRIBED_FILL = PatternFill("solid", fgColor="FFE599")
OPEN_FILL = PatternFill("solid", fgColor="F4CCCC")
THIN_GRAY = Side(style="thin", color="D9E1F2")
GRID_BORDER = Border(left=THIN_GRAY, right=THIN_GRAY, top=THIN_GRAY, bottom=THIN_GRAY)


def _open_rows(result: ScheduleResult, dates: tuple[date, ...]) -> list[tuple[str, dict[date, str]]]:
    maximum = max((len(result.open_shifts.get(day, ())) for day in dates), default=0)
    rows: list[tuple[str, dict[date, str]]] = []
    for row_index in range(maximum):
        name = "OPEN" if row_index == 0 else f"OPEN {row_index + 1}"
        assignments = {
            day: result.open_shifts[day][row_index]
            for day in dates
            if len(result.open_shifts.get(day, ())) > row_index
        }
        rows.append((name, assignments))
    return rows


def write_schedule_workbook(
    path: str | Path,
    config: ScheduleConfig,
    history: HistorySchedule,
    result: ScheduleResult,
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    schedule = workbook.active
    schedule.title = "Schedule"
    dates = config.dates

    schedule.cell(row=1, column=1, value="Doctor")
    for column, day in enumerate(dates, start=2):
        cell = schedule.cell(row=1, column=column, value=f"{day:%a}\n{day:%b %d}")
        cell.fill = WEEKEND_FILL if day.weekday() >= 5 else HEADER_FILL
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    rows = [(doctor.name, result.assignments[doctor.name]) for doctor in config.doctors]
    rows.extend(_open_rows(result, dates))
    doctor_by_name = {doctor.name: doctor for doctor in config.doctors}
    for row_number, (name, assignments) in enumerate(rows, start=2):
        name_cell = schedule.cell(row=row_number, column=1, value=name)
        name_cell.font = Font(bold=True)
        name_cell.fill = OPEN_FILL if name.upper().startswith("OPEN") else HEADER_FILL
        name_cell.font = Font(
            bold=True,
            color="000000" if name.upper().startswith("OPEN") else "FFFFFF",
        )
        doctor = doctor_by_name.get(name)
        unavailable = unavailable_dates_for(doctor, dates) if doctor else set()
        for column, day in enumerate(dates, start=2):
            cell = schedule.cell(row=row_number, column=column, value=assignments.get(day))
            cell.alignment = Alignment(horizontal="center", vertical="center")
            if name.upper().startswith("OPEN"):
                cell.fill = OPEN_FILL
            elif doctor and doctor.mode == DoctorMode.FIXED:
                cell.fill = FIXED_FILL
            elif doctor and day in doctor.assignments:
                cell.fill = PRESCRIBED_FILL
            elif day in unavailable:
                cell.fill = UNAVAILABLE_FILL

    for row in schedule.iter_rows(min_row=1, max_row=schedule.max_row, min_col=1, max_col=schedule.max_column):
        for cell in row:
            cell.border = GRID_BORDER
    schedule.freeze_panes = "B2"
    schedule.auto_filter.ref = f"A1:{get_column_letter(schedule.max_column)}{schedule.max_row}"
    schedule.column_dimensions["A"].width = 20
    for column in range(2, schedule.max_column + 1):
        schedule.column_dimensions[get_column_letter(column)].width = 12
    schedule.row_dimensions[1].height = 34
    schedule.sheet_view.showGridLines = False

    summary = workbook.create_sheet("Summary")
    headers = [
        "Doctor",
        "Mode",
        "# 8-6",
        "# 8-8",
        "# 2-12",
        "# O/N",
        "Total Shifts",
        "Days Off",
        "Total Hours",
        "Target Hours",
        "Hours/Week",
        "Weekend Shifts",
        "History O/N",
        "Combined O/N",
    ]
    summary.append(headers)
    weeks = len(dates) / 7
    for doctor in config.doctors:
        assignments = result.assignments[doctor.name]
        counts = {shift: sum(value == shift for value in assignments.values()) for shift in SHIFT_NAMES}
        total_shifts = sum(counts.values())
        total_hours = sum(SHIFT_HOURS[shift] * count for shift, count in counts.items())
        weekend_shifts = sum(day.weekday() >= 5 for day in assignments)
        history_overnights = result.history_overnights[doctor.name]
        summary.append(
            [
                doctor.name,
                doctor.mode.value.title(),
                counts["8-6"],
                counts["8-8"],
                counts["2-12"],
                counts["O/N"],
                total_shifts,
                len(dates) - total_shifts,
                total_hours,
                result.target_hours[doctor.name],
                round(total_hours / weeks, 1),
                weekend_shifts,
                history_overnights,
                history_overnights + counts["O/N"],
            ]
        )
    if result.open_shifts:
        open_counts = {shift: 0 for shift in SHIFT_NAMES}
        for shifts in result.open_shifts.values():
            for shift in shifts:
                open_counts[shift] += 1
        total = sum(open_counts.values())
        total_hours = sum(SHIFT_HOURS[shift] * count for shift, count in open_counts.items())
        summary.append(
            [
                "OPEN",
                "Automatic",
                open_counts["8-6"],
                open_counts["8-8"],
                open_counts["2-12"],
                open_counts["O/N"],
                total,
                "",
                total_hours,
                "",
                "",
                sum(
                    len(shifts)
                    for day, shifts in result.open_shifts.items()
                    if day.weekday() >= 5
                ),
                "",
                "",
            ]
        )
    _style_table(summary)

    details = workbook.create_sheet("Run Details")
    details.append(["Schedule Builder", "2.0"])
    details.append(["Schedule window", f"{config.start:%Y-%m-%d} to {config.end:%Y-%m-%d}"])
    details.append(["History workbook", str(history.source)])
    details.append(["History window", f"{history.dates[0]:%Y-%m-%d} to {history.dates[-1]:%Y-%m-%d}"])
    details.append(["History days imported", len(history.dates)])
    details.append([])
    details.append(["Optimization phase", "Status", "Objective", "Wall time (seconds)"])
    for report in result.phase_reports:
        details.append([report.name, report.status, report.objective, round(report.wall_time_seconds, 3)])
    details.append([])
    details.append(["Legend", "Meaning"])
    details.append(["Blue", "Fixed doctor schedule"])
    details.append(["Gold", "Prescribed assignment"])
    details.append(["Red", "Unavailable date or automatic OPEN shift"])
    details.append(["Green header", "Weekend"])
    details.append([])
    details.append(["OPEN priority (first used)", "Weekday 8-6, weekday 8-8, weekday 2-12, weekend 8-6, weekend 8-8, weekend 2-12, O/N"])
    _style_table(details, header_row=7)
    details.column_dimensions["A"].width = 28
    details.column_dimensions["B"].width = 95
    details.column_dimensions["C"].width = 18
    details.column_dimensions["D"].width = 20

    workbook.save(output)
    return output


def _style_table(sheet, header_row: int = 1) -> None:
    for cell in sheet[header_row]:
        if cell.value is not None:
            cell.fill = HEADER_FILL
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in sheet.iter_rows():
        for cell in row:
            if cell.value is not None:
                cell.border = GRID_BORDER
                if cell.row != header_row:
                    cell.alignment = Alignment(vertical="center")
    sheet.freeze_panes = f"A{header_row + 1}"
    sheet.sheet_view.showGridLines = False
    for column in range(1, sheet.max_column + 1):
        values = [len(str(sheet.cell(row=row, column=column).value or "")) for row in range(1, sheet.max_row + 1)]
        sheet.column_dimensions[get_column_letter(column)].width = min(max(values, default=8) + 2, 28)

