# Schedule Builder 2.0

Schedule Builder creates a new doctor roster from two inputs:

1. The previous schedule workbook, used as the complete scheduling history.
2. A JSON configuration describing the new period, doctors, protected assignments, availability, and rules.

It produces an Excel workbook in the same `Schedule`-sheet layout, so every generated workbook can become the history input for the following period.

## What is different

- The entire previous workbook is imported and validated, rather than manually copying three history days.
- Overnights are solved first. Historical and new O/N totals are balanced across capable doctors.
- Two-night blocks are preferred. Three-night blocks are allowed with four recovery nights. Single overnights are allowed only with a very large optimization penalty and require three recovery nights.
- `fixed`, `prescribed`, and `default` doctor modes are explicit.
- Doctor rules are configuration data rather than monthly edits to optimizer code.
- Missing coverage becomes dynamic `OPEN`, `OPEN 2`, etc. rows. No OPEN doctor needs to be configured.
- Weekend pairs are strongly preferred, not mandatory.
- The optimizer and Excel layer sit behind `ScheduleBuilderService`, which is a stable entry point for a future GUI.

## Installation

From this folder:

```powershell
py -3.12 -m pip install -e .
```

The required libraries are OR-Tools and openpyxl.

## Building a schedule

```powershell
py -3.12 -m schedule_builder validate `
  --history "Aug24_Sep20_Schedule.xlsx" `
  --config "examples/sep21_oct18_2026.json"

py -3.12 -m schedule_builder build `
  --history "Aug24_Sep20_Schedule.xlsx" `
  --config "examples/sep21_oct18_2026.json" `
  --output "Sep21_Oct18_Schedule.xlsx"
```

The history workbook must end on the day immediately before the new schedule starts. Date headers are checked, and every visible column is imported. `OPEN` rows in the history are recorded as uncovered history but are not treated as doctors.

## Doctor modes

### Default

The optimizer generates the entire schedule.

```json
{
  "name": "Emily",
  "mode": "default",
  "overnight_capable": true,
  "rules": []
}
```

### Prescribed

Listed assignments are immutable. Other dates are generated normally. Prescribed dates override availability, rolling-work, transition, weekend-pair, and recovery restrictions when necessary.

```json
{
  "name": "Adrian",
  "mode": "prescribed",
  "overnight_capable": false,
  "assignments": {
    "2026-10-15": "8-6"
  },
  "rules": [
    {"type": "allowed_shifts", "shifts": ["8-6"]}
  ]
}
```

### Fixed

Only listed assignments are worked; every unlisted date is fixed OFF. Normal rest and workload rules are not applied to the doctor.

```json
{
  "name": "Jon",
  "mode": "fixed",
  "overnight_capable": false,
  "assignments": {
    "2026-09-23": "2-12",
    "2026-09-24": "2-12"
  },
  "rules": []
}
```

Fixed or prescribed O/N assignments still require `overnight_capable: true`. This is treated as a qualification rather than an ordinary scheduling preference.

## Built-in doctor rules

Rules are added to a doctor's `rules` list. Supported categories are:

| Rule | Fields | Purpose |
|---|---|---|
| `allowed_weekdays` | `weekdays` | Work only on named weekdays. |
| `unavailable_dates` | `dates` | Vacation or other individual dates off. |
| `start_date` | `date` | Joining partway through a schedule. |
| `end_date` | `date` | Leaving partway through a schedule. |
| `forbidden_shifts` | `shifts` | Never assign selected shift types. |
| `allowed_shifts` | `shifts` | Assign only selected shift types. |
| `forbidden_shift_weekdays` | `shifts`, `weekdays` | Forbid selected shifts on selected weekdays. |
| `max_total_shifts` | `value` | Cap automatically generated shifts. |
| `max_overnights` | `value` | Set an individual O/N cap. |
| `max_weekend_days` | `value` | Cap automatically generated weekend days. |
| `max_consecutive_days` | `value` | Add a doctor-specific consecutive-day limit. |
| `rolling_limit` | `window_days`, `max_shifts` | Cap shifts in every rolling window. |

Nancy working only Thursday and Friday is represented as:

```json
{"type": "allowed_weekdays", "weekdays": ["Thu", "Fri"]}
```

A doctor joining October 10 is represented as:

```json
{"type": "start_date", "date": "2026-10-10"}
```

New rule categories can be added by registering a handler in `schedule_builder/rules.py`; configuration parsing and constraint logic remain isolated from Excel and the command line.

## Optimization order

The model uses three phases:

1. **Overnights:** avoid OPEN O/N, avoid singleton nights, balance combined history/new O/N totals, and prefer two-night over three-night blocks.
2. **OPEN priority:** freeze the exact best uncovered-shift distribution. When OPEN is unavoidable, it is used in this order: weekday `8-6`, weekday `8-8`, weekday `2-12`, weekend `8-6`, weekend `8-8`, weekend `2-12`, then O/N.
3. **Schedule quality:** balance visible-period hours, strongly prefer Saturday/Sunday pairs, and discourage isolated workdays.

Because earlier decisions are frozen, hour balancing cannot undo a better overnight plan or move an OPEN shift into a less desirable category.

## Output workbook

- `Schedule`: next month's reusable schedule grid.
- `Summary`: visible shift counts, hours, targets, weekend totals, history O/N, and combined O/N.
- `Run Details`: history range, phase status, solve times, and formatting legend.

Formatting distinguishes fixed doctors, prescribed assignments, unavailable dates, weekends, and OPEN shifts. The Schedule sheet freezes names and dates for easier navigation.

## Tests

```powershell
py -3.12 -m unittest discover -v
```

The tests cover configuration validation, full-workbook history import, protected assignments, automatic OPEN priority, and relaxed weekend pairing.
