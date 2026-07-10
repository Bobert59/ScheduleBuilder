from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .errors import ScheduleBuilderError
from .service import ScheduleBuilderService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schedule-builder",
        description="Build a doctor schedule from the previous schedule workbook and a JSON configuration.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    validate = subcommands.add_parser("validate", help="Validate the configuration and history workbook.")
    validate.add_argument("--history", required=True, type=Path, help="Previous schedule .xlsx file")
    validate.add_argument("--config", required=True, type=Path, help="New schedule JSON configuration")

    build = subcommands.add_parser("build", help="Optimize and write a new schedule workbook.")
    build.add_argument("--history", required=True, type=Path, help="Previous schedule .xlsx file")
    build.add_argument("--config", required=True, type=Path, help="New schedule JSON configuration")
    build.add_argument("--output", required=True, type=Path, help="Output .xlsx file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    service = ScheduleBuilderService()
    try:
        if args.command == "validate":
            config, history = service.validate(args.history, args.config)
            print(
                f"Valid: {len(config.doctors)} doctors, {len(config.dates)} schedule days, "
                f"{len(history.dates)} history days ({history.dates[0]} to {history.dates[-1]})."
            )
        else:
            outcome = service.build(args.history, args.config, args.output)
            open_count = sum(len(shifts) for shifts in outcome.result.open_shifts.values())
            print(f"Schedule written to {outcome.output_path}")
            print(f"Automatic OPEN shifts: {open_count}")
            for report in outcome.result.phase_reports:
                print(
                    f"  {report.name}: {report.status}, objective={report.objective:g}, "
                    f"time={report.wall_time_seconds:.2f}s"
                )
        return 0
    except ScheduleBuilderError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

