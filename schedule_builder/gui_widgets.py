from __future__ import annotations

import calendar
import copy
import tkinter as tk
from datetime import date, timedelta
from tkinter import messagebox, ttk
from typing import Any

from .domain import SHIFT_NAMES
from .gui_state import RULE_LABELS, WEEKDAY_LABELS, time_off_label


def parse_date(value: str | None, fallback: date | None = None) -> date:
    if value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            try:
                year, month, day = (int(part) for part in value.split("-"))
                return date(year, month, day)
            except (TypeError, ValueError):
                pass
    return fallback or date.today()


class CalendarPicker(ttk.Frame):
    def __init__(
        self,
        parent,
        *,
        start: date | None = None,
        end: date | None = None,
        allow_range: bool = True,
    ):
        super().__init__(parent)
        self.allow_range = allow_range
        self.start = start
        self.end = end if allow_range else start
        focus = start or date.today()
        self.year = focus.year
        self.month = focus.month
        self.title_var = tk.StringVar()
        self._buttons: list[tk.Button] = []

        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 6))
        ttk.Button(header, text="◀", width=4, command=lambda: self._move_month(-1)).pack(side="left")
        ttk.Label(header, textvariable=self.title_var, anchor="center", font=("Segoe UI", 11, "bold")).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(header, text="▶", width=4, command=lambda: self._move_month(1)).pack(side="right")

        grid = ttk.Frame(self)
        grid.pack()
        for column, label in enumerate(WEEKDAY_LABELS):
            ttk.Label(grid, text=label, width=5, anchor="center").grid(row=0, column=column, padx=1, pady=1)
        for index in range(42):
            button = tk.Button(
                grid,
                width=4,
                height=1,
                relief="flat",
                font=("Segoe UI", 9),
                command=lambda i=index: self._select_index(i),
            )
            button.grid(row=index // 7 + 1, column=index % 7, padx=1, pady=1)
            self._buttons.append(button)
        self._render()

    def _move_month(self, delta: int) -> None:
        value = self.year * 12 + self.month - 1 + delta
        self.year, month_index = divmod(value, 12)
        self.month = month_index + 1
        self._render()

    def _month_dates(self) -> list[date | None]:
        first_weekday, days = calendar.monthrange(self.year, self.month)
        values: list[date | None] = [None] * first_weekday
        values.extend(date(self.year, self.month, day) for day in range(1, days + 1))
        values.extend([None] * (42 - len(values)))
        return values

    def _select_index(self, index: int) -> None:
        chosen = self._month_dates()[index]
        if chosen is None:
            return
        if not self.allow_range:
            self.start = self.end = chosen
        elif self.start is None or self.end is not None:
            self.start = chosen
            self.end = None
        else:
            self.start, self.end = sorted((self.start, chosen))
        self._render()

    def _render(self) -> None:
        self.title_var.set(f"{calendar.month_name[self.month]} {self.year}")
        today = date.today()
        for button, day in zip(self._buttons, self._month_dates(), strict=True):
            if day is None:
                button.configure(text="", state="disabled", bg="#f4f4f4")
                continue
            background = "white"
            foreground = "black"
            if self.start and self.end and self.start <= day <= self.end:
                background = "#dbeafe"
            if day in {self.start, self.end}:
                background = "#2563eb"
                foreground = "white"
            elif day == today:
                background = "#e5e7eb"
            button.configure(text=str(day.day), state="normal", bg=background, fg=foreground)

    def selection(self) -> tuple[date | None, date | None]:
        if self.start and self.end is None:
            return self.start, self.start
        return self.start, self.end


class CalendarDialog(tk.Toplevel):
    def __init__(
        self,
        parent,
        title: str,
        *,
        start: date | None = None,
        end: date | None = None,
        allow_range: bool = True,
    ):
        super().__init__(parent)
        self.result: tuple[date, date] | None = None
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        instruction = (
            "Click the first and last date. Click a new date to restart the selection."
            if allow_range
            else "Select a date."
        )
        ttk.Label(body, text=instruction, foreground="#4b5563").pack(anchor="w", pady=(0, 8))
        self.picker = CalendarPicker(body, start=start, end=end, allow_range=allow_range)
        self.picker.pack()
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Use selection", command=self._accept).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _event: self.destroy())
        self.wait_visibility()
        self.focus_set()

    def _accept(self) -> None:
        start, end = self.picker.selection()
        if start is None or end is None:
            messagebox.showwarning("Select a date", "Choose a date before continuing.", parent=self)
            return
        self.result = (start, end)
        self.destroy()


class DateField(ttk.Frame):
    def __init__(self, parent, variable: tk.StringVar, *, width: int = 14):
        super().__init__(parent)
        self.variable = variable
        ttk.Entry(self, textvariable=variable, width=width).pack(side="left", fill="x", expand=True)
        ttk.Button(self, text="📅", width=3, command=self._choose).pack(side="left", padx=(4, 0))

    def _choose(self) -> None:
        dialog = CalendarDialog(
            self,
            "Select date",
            start=parse_date(self.variable.get()),
            allow_range=False,
        )
        self.wait_window(dialog)
        if dialog.result:
            self.variable.set(dialog.result[0].isoformat())


class AssignmentDialog(tk.Toplevel):
    def __init__(self, parent, *, initial_date: date | None = None, initial_shift: str = "8-6"):
        super().__init__(parent)
        self.result: tuple[date, date, str] | None = None
        self.title("Add fixed or prescribed assignment")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="Choose one date, or click a start and end date to assign the same shift across a range.",
            wraplength=420,
        ).pack(anchor="w", pady=(0, 8))
        self.picker = CalendarPicker(
            body,
            start=initial_date,
            end=initial_date,
            allow_range=True,
        )
        self.picker.pack()
        shift_row = ttk.Frame(body)
        shift_row.pack(fill="x", pady=(10, 0))
        ttk.Label(shift_row, text="Shift:").pack(side="left")
        self.shift_var = tk.StringVar(value=initial_shift)
        ttk.Combobox(
            shift_row,
            textvariable=self.shift_var,
            values=SHIFT_NAMES,
            state="readonly",
            width=10,
        ).pack(side="left", padx=(8, 0))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Add assignment", command=self._accept).pack(side="right", padx=(0, 8))

    def _accept(self) -> None:
        start, end = self.picker.selection()
        if not start or not end:
            messagebox.showwarning("Select dates", "Select at least one date.", parent=self)
            return
        self.result = (start, end, self.shift_var.get())
        self.destroy()


class RuleDialog(tk.Toplevel):
    TYPES = tuple(RULE_LABELS)
    TYPE_TO_LABEL = dict(RULE_LABELS)
    LABEL_TO_TYPE = {label: key for key, label in RULE_LABELS.items()}

    def __init__(self, parent, rule: dict[str, Any] | None = None):
        super().__init__(parent)
        self.result: dict[str, Any] | None = None
        self.original = copy.deepcopy(rule) if rule else None
        self.title("Doctor rule")
        self.geometry("560x610")
        self.minsize(520, 540)
        self.transient(parent)
        self.grab_set()

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Rule type:").pack(anchor="w")
        initial_type = (rule or {}).get("type", self.TYPES[0])
        self.type_var = tk.StringVar(value=self.TYPE_TO_LABEL.get(initial_type, str(initial_type)))
        selector = ttk.Combobox(
            body,
            textvariable=self.type_var,
            values=tuple(self.TYPE_TO_LABEL[key] for key in self.TYPES),
            state="readonly",
        )
        selector.pack(fill="x", pady=(4, 10))
        selector.bind("<<ComboboxSelected>>", lambda _event: self._render_editor())
        self.editor = ttk.LabelFrame(body, text="Rule settings", padding=10)
        self.editor.pack(fill="both", expand=True)
        self.controls: dict[str, Any] = {}

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Save rule", command=self._accept).pack(side="right", padx=(0, 8))
        self._render_editor()

    def _render_editor(self) -> None:
        for child in self.editor.winfo_children():
            child.destroy()
        self.controls = {}
        rule_type = self.LABEL_TO_TYPE.get(self.type_var.get(), self.type_var.get())
        existing = self.original if self.original and self.original.get("type") == rule_type else {}

        if rule_type in {"allowed_weekdays", "forbidden_shift_weekdays"}:
            self._weekday_controls(existing.get("weekdays", []))
        if rule_type in {"forbidden_shifts", "allowed_shifts", "forbidden_shift_weekdays"}:
            self._shift_controls(existing.get("shifts", []))
        if rule_type == "unavailable_dates":
            self._date_list_controls(existing.get("dates", []))
        elif rule_type in {"start_date", "end_date"}:
            ttk.Label(self.editor, text="Date:").pack(anchor="w")
            variable = tk.StringVar(value=str(existing.get("date", date.today().isoformat())))
            DateField(self.editor, variable).pack(fill="x", pady=(4, 0))
            self.controls["date"] = variable
        elif rule_type == "rolling_limit":
            self._number_control("window_days", "Window length (days)", existing.get("window_days", 5))
            self._number_control("max_shifts", "Maximum shifts in window", existing.get("max_shifts", 3))
        elif rule_type in {
            "max_total_shifts",
            "max_overnights",
            "max_overnight_block_length",
            "max_weekend_days",
            "max_consecutive_days",
        }:
            default = 2 if rule_type == "max_overnight_block_length" else existing.get("value", 1)
            self._number_control("value", "Maximum", existing.get("value", default))

    def _weekday_controls(self, selected: list[Any]) -> None:
        frame = ttk.LabelFrame(self.editor, text="Weekdays", padding=6)
        frame.pack(fill="x", pady=(0, 8))
        normalized = {str(value)[:3].title() for value in selected}
        variables = {}
        for index, label in enumerate(WEEKDAY_LABELS):
            variable = tk.BooleanVar(value=label in normalized)
            ttk.Checkbutton(frame, text=label, variable=variable).grid(row=0, column=index, padx=4)
            variables[label] = variable
        self.controls["weekdays"] = variables

    def _shift_controls(self, selected: list[str]) -> None:
        frame = ttk.LabelFrame(self.editor, text="Shifts", padding=6)
        frame.pack(fill="x", pady=(0, 8))
        variables = {}
        for index, shift in enumerate(SHIFT_NAMES):
            variable = tk.BooleanVar(value=shift in selected)
            ttk.Checkbutton(frame, text=shift, variable=variable).grid(row=0, column=index, padx=8)
            variables[shift] = variable
        self.controls["shifts"] = variables

    def _number_control(self, key: str, label: str, value: Any) -> None:
        row = ttk.Frame(self.editor)
        row.pack(fill="x", pady=5)
        ttk.Label(row, text=label).pack(side="left")
        variable = tk.StringVar(value=str(value))
        ttk.Spinbox(row, from_=0, to=365, textvariable=variable, width=8).pack(side="right")
        self.controls[key] = variable

    def _date_list_controls(self, dates: list[Any]) -> None:
        self.date_items = [str(value) for value in dates]
        frame = ttk.Frame(self.editor)
        frame.pack(fill="both", expand=True)
        self.date_tree = ttk.Treeview(frame, columns=("range",), show="headings", height=10)
        self.date_tree.heading("range", text="Unavailable date or range")
        self.date_tree.column("range", width=360)
        self.date_tree.pack(side="left", fill="both", expand=True)
        controls = ttk.Frame(frame)
        controls.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(controls, text="Add…", command=self._add_rule_dates).pack(fill="x")
        ttk.Button(controls, text="Remove", command=self._remove_rule_dates).pack(fill="x", pady=(6, 0))
        self._refresh_rule_dates()
        self.controls["dates"] = self.date_items

    def _add_rule_dates(self) -> None:
        dialog = CalendarDialog(self, "Unavailable dates", allow_range=True)
        self.wait_window(dialog)
        if not dialog.result:
            return
        start, end = dialog.result
        for offset in range((end - start).days + 1):
            value = (start + timedelta(days=offset)).isoformat()
            if value not in self.date_items:
                self.date_items.append(value)
        self._refresh_rule_dates()

    def _remove_rule_dates(self) -> None:
        selected = [int(item) for item in self.date_tree.selection()]
        for index in sorted(selected, reverse=True):
            self.date_items.pop(index)
        self._refresh_rule_dates()

    def _refresh_rule_dates(self) -> None:
        for item in self.date_tree.get_children():
            self.date_tree.delete(item)
        for index, value in enumerate(self.date_items):
            self.date_tree.insert("", "end", iid=str(index), values=(time_off_label(value),))

    def _accept(self) -> None:
        result: dict[str, Any] = {
            "type": self.LABEL_TO_TYPE.get(self.type_var.get(), self.type_var.get())
        }
        try:
            for key, control in self.controls.items():
                if key in {"weekdays", "shifts"}:
                    values = [name for name, variable in control.items() if variable.get()]
                    if not values:
                        raise ValueError(f"Select at least one {key} value.")
                    result[key] = values
                elif key == "dates":
                    result[key] = list(self.date_items)
                elif key == "date":
                    result[key] = parse_date(control.get()).isoformat()
                else:
                    result[key] = int(control.get())
        except ValueError as exc:
            messagebox.showwarning("Incomplete rule", str(exc), parent=self)
            return
        self.result = result
        self.destroy()
