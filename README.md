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

### Windows desktop app

Double-click `Launch Schedule Builder.vbs` for a normal window without a console. The `.bat` launcher is also available for troubleshooting, or run:

```powershell
py -3.12 -m schedule_builder.gui
```

The app provides four screens:

1. **Setup:** select the previous workbook, schedule dates, output location, and target-hour settings.
2. **Doctors:** add/edit doctors, choose Default/Prescribed/Fixed mode, select vacation ranges on a calendar, enter protected shifts, and manage doctor-specific rules.
3. **Advanced settings:** edit default rest rules, weekend penalties, and solver settings.
4. **Generate:** validate inputs and run the optimizer without freezing the interface. A live elapsed-time counter remains visible during generation, and **Stop** cancels the active solver run without writing a new workbook.

Configurations can be opened and saved as JSON, so files created in the GUI remain compatible with the command line. Fixed and prescribed assignment ranges, vacation ranges, join/leave dates, and other date-based rules use calendar dialogs.

### Command line

```powershell
py -3.12 -m schedule_builder validate `
  --history "Aug24_Sep20_Schedule.xlsx" `
  --config "examples/sep21_oct18_2026.json"

py -3.12 -m schedule_builder build `
  --history "Aug24_Sep20_Schedule.xlsx" `
  --config "examples/sep21_oct18_2026.json" `
  --output "Sep21_Oct18_Schedule.xlsx"
```

The history workbook must end on the day immediately before the new schedule starts. Date headers are checked, and every visible column is imported. The reader accepts either combined headers (`Mon` and `Aug 24` in one cell) or the final two-row layout with weekdays above month/day values. `OPEN` rows in the history are recorded as uncovered history but are not treated as doctors.

## Vacation and time off

Add a `time_off` list directly to any doctor:

```json
{
  "name": "Dr. Avery",
  "mode": "default",
  "overnight_capable": true,
  "time_off": [
    "2026-09-28",
    {
      "start": "2026-10-05",
      "end": "2026-10-16"
    }
  ],
  "rules": []
}
```

Individual dates and inclusive ranges can be mixed in the same list. In the example, September 28 and every date from October 5 through October 16 are unavailable. All dates must fall inside the new schedule window. A prescribed assignment on the same date takes priority. Fixed doctors work only their listed assignments, so their `time_off` list is normally empty.

## Doctor modes

### Default

The optimizer generates the entire schedule.

```json
{
  "name": "Dr. Avery",
  "mode": "default",
  "overnight_capable": true,
  "rules": []
}
```

### Prescribed

Listed assignments are immutable. Other dates are generated normally. Prescribed dates override availability, rolling-work, transition, weekend-pair, and recovery restrictions when necessary.

```json
{
  "name": "Dr. Irving",
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
  "name": "Dr. Lane",
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
| `max_overnights` | `value` | Cap the total automatically generated O/N shifts in the new schedule. |
| `max_overnight_block_length` | `value` | Limit O/N blocks to 1, 2, or 3 nights, including across the history boundary. |
| `max_weekend_days` | `value` | Cap automatically generated weekend days. |
| `max_consecutive_days` | `value` | Add a doctor-specific consecutive-day limit. |
| `rolling_limit` | `window_days`, `max_shifts` | Cap shifts in every rolling window. |

A doctor working only Thursday and Friday is represented as:

```json
{"type": "allowed_weekdays", "weekdays": ["Thu", "Fri"]}
```

A doctor joining October 10 is represented as:

```json
{"type": "start_date", "date": "2026-10-10"}
```

A doctor allowing one- or two-night O/N blocks, but never three nights:

```json
{"type": "max_overnight_block_length", "value": 2}
```

New rule categories can be added by registering a handler in `schedule_builder/rules.py`; configuration parsing and constraint logic remain isolated from Excel and the command line.

## Optimization order

The model uses three phases:

1. **Overnights:** avoid OPEN O/N, avoid singleton nights, balance combined history/new O/N totals, and prefer two-night over three-night blocks.
2. **Coverage and weekends:** jointly weigh OPEN placement against Saturday/Sunday splits. Weekday OPEN shifts are cheaper than split weekends, while weekend OPEN shifts are much more expensive. `max_weekend_shifts` is a hard doctor-specific cap and defaults to four shifts (normally two Saturday/Sunday pairs).
3. **Schedule quality:** balance visible-period hours, discourage isolated workdays, and prefer 8-8 shifts in two-day blocks without undoing the earlier decisions.

`quality_weights.weekend_single` controls how strongly a split weekend is discouraged. Its default working value is `10000`; raising it makes the model more willing to leave weekday shifts OPEN to avoid splits, while lowering it favors coverage. Weekend OPEN costs scale above this value, so increasing it does not make weekend coverage casually disappear.

The 12-hour shifts prefer two-day blocks. O/N grouping is handled first and remains strict: singleton nights are heavily discouraged and three-night blocks receive a small final tie-break penalty. For 8-8 shifts, `quality_weights.shift_88_singleton` (default `250`) discourages isolated shifts and `quality_weights.shift_88_triple` (default `25`) lightly discourages three-day blocks. These are preferences rather than hard limits, and they include an 8-8 block that begins in the imported history. The grouping preference does not apply to 8-6 or 2-12 shifts.

Work-streak and rolling-window limits include the end of the imported history period. An O/N shift is also forbidden on the day immediately before an explicit vacation or unavailable date because that shift continues into the following morning.

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
