from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event

from .config import load_config
from .domain import HistorySchedule, ScheduleConfig, ScheduleResult
from .excel import write_schedule_workbook
from .errors import ScheduleCancelledError
from .history import read_history_workbook
from .optimizer import ScheduleOptimizer


@dataclass(frozen=True)
class BuildOutcome:
    output_path: Path
    config: ScheduleConfig
    history: HistorySchedule
    result: ScheduleResult


class ScheduleBuilderService:
    """Application boundary intended to remain stable when a GUI is added."""

    def validate(self, history_path: str | Path, config_path: str | Path) -> tuple[ScheduleConfig, HistorySchedule]:
        config = load_config(config_path)
        from datetime import timedelta

        history = read_history_workbook(history_path, expected_end=config.start - timedelta(days=1))
        return config, history

    def build(
        self,
        history_path: str | Path,
        config_path: str | Path,
        output_path: str | Path,
        cancel_event: Event | None = None,
    ) -> BuildOutcome:
        config, history = self.validate(history_path, config_path)
        if cancel_event is not None and cancel_event.is_set():
            raise ScheduleCancelledError("Schedule generation was stopped.")
        result = ScheduleOptimizer(config, history, cancel_event=cancel_event).solve()
        if cancel_event is not None and cancel_event.is_set():
            raise ScheduleCancelledError("Schedule generation was stopped.")
        output = write_schedule_workbook(output_path, config, history, result)
        return BuildOutcome(output_path=output, config=config, history=history, result=result)
