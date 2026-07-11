from __future__ import annotations

import argparse
import copy
import json
import queue
import tempfile
import threading
import time
import tkinter as tk
from datetime import date, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .errors import ScheduleBuilderError, ScheduleCancelledError
from .gui_state import (
    format_elapsed,
    new_config,
    new_doctor,
    normalize_config,
    rule_label,
    suggested_output_name,
    time_off_label,
)
from .gui_widgets import AssignmentDialog, CalendarDialog, DateField, RuleDialog, parse_date
from .history import read_history_workbook
from .service import ScheduleBuilderService


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEDULES_DIR = PROJECT_ROOT / "Schedules"


class ScrollableFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        canvas = tk.Canvas(self, highlightthickness=0, background="#ffffff")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.content = ttk.Frame(canvas, padding=12)
        window = canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind(
            "<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.bind_all(
            "<MouseWheel>",
            lambda event: canvas.yview_scroll(int(-event.delta / 120), "units")
            if canvas.winfo_ismapped()
            else None,
        )


class ScheduleBuilderApp(ttk.Frame):
    def __init__(self, root: tk.Tk):
        super().__init__(root)
        self.root = root
        self.root.title("Doctor Schedule Builder")
        self.root.geometry("1180x780")
        self.root.minsize(980, 680)
        self.pack(fill="both", expand=True)
        self.service = ScheduleBuilderService()
        self.config: dict[str, Any] = new_config()
        self.config_path: Path | None = None
        self.current_doctor_index: int | None = None
        self.current_time_off: list[Any] = []
        self.current_assignments: dict[str, str] = {}
        self.current_rules: list[dict[str, Any]] = []
        self._loading_doctor = False
        self._task_queue: queue.Queue = queue.Queue()
        self._busy = False
        self._active_task: str | None = None
        self._task_cancel_event: threading.Event | None = None
        self._task_started_at: float | None = None

        self._configure_style()
        self._build_toolbar()
        self._build_notebook()
        self._load_config_into_ui(self.config)
        self._load_initial_config()

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Heading.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"))

    def _build_toolbar(self) -> None:
        toolbar = ttk.Frame(self, padding=(10, 8))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="Doctor Schedule Builder", style="Title.TLabel").pack(side="left")
        ttk.Button(toolbar, text="New", command=self._new_config).pack(side="right", padx=(6, 0))
        ttk.Button(toolbar, text="Open config…", command=self._open_config).pack(side="right", padx=(6, 0))
        ttk.Button(toolbar, text="Save", command=self._save_config).pack(side="right", padx=(6, 0))
        ttk.Button(toolbar, text="Save as…", command=lambda: self._save_config(save_as=True)).pack(
            side="right", padx=(6, 0)
        )

    def _build_notebook(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.setup_tab = ttk.Frame(self.notebook, padding=16)
        self.doctors_tab = ttk.Frame(self.notebook)
        self.advanced_tab = ScrollableFrame(self.notebook)
        self.run_tab = ttk.Frame(self.notebook, padding=16)
        self.notebook.add(self.setup_tab, text="1. Setup")
        self.notebook.add(self.doctors_tab, text="2. Doctors")
        self.notebook.add(self.advanced_tab, text="3. Advanced settings")
        self.notebook.add(self.run_tab, text="4. Generate")
        self._build_setup_tab()
        self._build_doctors_tab()
        self._build_advanced_tab()
        self._build_run_tab()

    @staticmethod
    def _labelled_row(parent, row: int, label: str, widget, help_text: str = "") -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=7)
        widget.grid(row=row, column=1, sticky="ew", pady=7)
        if help_text:
            ttk.Label(parent, text=help_text, foreground="#6b7280", wraplength=420).grid(
                row=row, column=2, sticky="w", padx=(12, 0), pady=7
            )

    def _build_setup_tab(self) -> None:
        ttk.Label(self.setup_tab, text="Schedule setup", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 12)
        )
        self.setup_tab.columnconfigure(1, weight=1)
        self.history_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.start_var = tk.StringVar()
        self.end_var = tk.StringVar()
        self.base_hours_var = tk.StringVar()
        self.prorate_var = tk.StringVar()

        history = ttk.Frame(self.setup_tab)
        ttk.Entry(history, textvariable=self.history_var).pack(side="left", fill="x", expand=True)
        ttk.Button(history, text="Browse…", command=self._browse_history).pack(side="left", padx=(8, 0))
        self._labelled_row(
            self.setup_tab,
            1,
            "Previous schedule workbook",
            history,
            "The complete previous schedule is imported as history.",
        )
        self._labelled_row(self.setup_tab, 2, "Schedule start", DateField(self.setup_tab, self.start_var))
        self._labelled_row(self.setup_tab, 3, "Schedule end", DateField(self.setup_tab, self.end_var))

        output = ttk.Frame(self.setup_tab)
        ttk.Entry(output, textvariable=self.output_var).pack(side="left", fill="x", expand=True)
        ttk.Button(output, text="Browse…", command=self._browse_output).pack(side="left", padx=(8, 0))
        self._labelled_row(self.setup_tab, 4, "Output workbook", output)
        self._labelled_row(
            self.setup_tab,
            5,
            "Base target hours",
            ttk.Entry(self.setup_tab, textvariable=self.base_hours_var, width=12),
            "Default target for a full schedule period.",
        )
        self._labelled_row(
            self.setup_tab,
            6,
            "Prorate after unavailable days",
            ttk.Entry(self.setup_tab, textvariable=self.prorate_var, width=12),
            "Vacation starts reducing target hours at this many unavailable days.",
        )
        ttk.Separator(self.setup_tab).grid(row=7, column=0, columnspan=3, sticky="ew", pady=18)
        ttk.Label(
            self.setup_tab,
            text=(
                "Next: add or edit doctors. Dates in the workbook must end one day before the new "
                "schedule starts. The Validate button on the final tab checks this for you."
            ),
            wraplength=760,
            foreground="#374151",
        ).grid(row=8, column=0, columnspan=3, sticky="w")

    def _build_doctors_tab(self) -> None:
        pane = ttk.Panedwindow(self.doctors_tab, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left = ttk.Frame(pane, padding=10)
        right = ScrollableFrame(pane)
        pane.add(left, weight=1)
        pane.add(right, weight=3)

        ttk.Label(left, text="Doctors", style="Heading.TLabel").pack(anchor="w", pady=(0, 8))
        self.doctor_tree = ttk.Treeview(left, columns=("mode", "on"), show="tree headings", selectmode="browse")
        self.doctor_tree.heading("#0", text="Name")
        self.doctor_tree.heading("mode", text="Mode")
        self.doctor_tree.heading("on", text="O/N")
        self.doctor_tree.column("#0", width=150)
        self.doctor_tree.column("mode", width=85, anchor="center")
        self.doctor_tree.column("on", width=45, anchor="center")
        self.doctor_tree.pack(fill="both", expand=True)
        self.doctor_tree.bind("<<TreeviewSelect>>", self._doctor_selected)
        doctor_buttons = ttk.Frame(left)
        doctor_buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(doctor_buttons, text="Add", command=self._add_doctor).pack(side="left")
        ttk.Button(doctor_buttons, text="Duplicate", command=self._duplicate_doctor).pack(side="left", padx=4)
        ttk.Button(doctor_buttons, text="Delete", command=self._delete_doctor).pack(side="left")

        editor = right.content
        editor.columnconfigure(1, weight=1)
        ttk.Label(editor, text="Doctor details", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )
        self.doctor_name_var = tk.StringVar()
        self.doctor_mode_var = tk.StringVar(value="default")
        self.doctor_overnight_var = tk.BooleanVar()
        self.doctor_target_var = tk.StringVar()
        self.doctor_defaults_var = tk.BooleanVar(value=True)
        self.doctor_weekends_var = tk.StringVar()
        ttk.Label(editor, text="Name").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(editor, textvariable=self.doctor_name_var).grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Label(editor, text="Mode").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Combobox(
            editor,
            textvariable=self.doctor_mode_var,
            values=("default", "prescribed", "fixed"),
            state="readonly",
        ).grid(row=2, column=1, sticky="ew", pady=5)
        ttk.Checkbutton(editor, text="Qualified for overnight shifts", variable=self.doctor_overnight_var).grid(
            row=3, column=1, sticky="w", pady=5
        )
        ttk.Checkbutton(editor, text="Apply default rest rules", variable=self.doctor_defaults_var).grid(
            row=4, column=1, sticky="w", pady=5
        )
        ttk.Label(editor, text="Target hours (optional)").grid(row=5, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(editor, textvariable=self.doctor_target_var).grid(row=5, column=1, sticky="ew", pady=5)
        ttk.Label(editor, text="Maximum weekend shifts").grid(row=6, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(editor, textvariable=self.doctor_weekends_var).grid(row=6, column=1, sticky="ew", pady=5)
        ttk.Label(
            editor,
            text="Fixed: only listed assignments are worked. Prescribed: listed assignments are locked; other dates are generated.",
            foreground="#6b7280",
            wraplength=650,
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(4, 12))

        self._build_time_off_section(editor, row=8)
        self._build_assignment_section(editor, row=10)
        self._build_rule_section(editor, row=12)
        ttk.Button(editor, text="Apply doctor changes", command=self._apply_doctor_changes).grid(
            row=14, column=1, sticky="e", pady=(14, 4)
        )

    def _build_time_off_section(self, parent, row: int) -> None:
        frame = ttk.LabelFrame(parent, text="Vacation and time off", padding=8)
        frame.grid(row=row, column=0, columnspan=2, sticky="nsew", pady=6)
        self.time_off_tree = ttk.Treeview(frame, columns=("dates",), show="headings", height=5)
        self.time_off_tree.heading("dates", text="Unavailable date or inclusive range")
        self.time_off_tree.pack(side="left", fill="both", expand=True)
        buttons = ttk.Frame(frame)
        buttons.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(buttons, text="Add range…", command=self._add_time_off).pack(fill="x")
        ttk.Button(buttons, text="Edit…", command=self._edit_time_off).pack(fill="x", pady=(5, 0))
        ttk.Button(buttons, text="Remove", command=self._remove_time_off).pack(fill="x", pady=(5, 0))

    def _build_assignment_section(self, parent, row: int) -> None:
        frame = ttk.LabelFrame(parent, text="Fixed or prescribed assignments", padding=8)
        frame.grid(row=row, column=0, columnspan=2, sticky="nsew", pady=6)
        self.assignment_tree = ttk.Treeview(frame, columns=("date", "shift"), show="headings", height=7)
        self.assignment_tree.heading("date", text="Date")
        self.assignment_tree.heading("shift", text="Shift")
        self.assignment_tree.column("date", width=130)
        self.assignment_tree.column("shift", width=80)
        self.assignment_tree.pack(side="left", fill="both", expand=True)
        buttons = ttk.Frame(frame)
        buttons.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(buttons, text="Add…", command=self._add_assignment).pack(fill="x")
        ttk.Button(buttons, text="Edit…", command=self._edit_assignment).pack(fill="x", pady=(5, 0))
        ttk.Button(buttons, text="Remove", command=self._remove_assignment).pack(fill="x", pady=(5, 0))

    def _build_rule_section(self, parent, row: int) -> None:
        frame = ttk.LabelFrame(parent, text="Doctor-specific rules", padding=8)
        frame.grid(row=row, column=0, columnspan=2, sticky="nsew", pady=6)
        self.rule_tree = ttk.Treeview(frame, columns=("rule",), show="headings", height=7)
        self.rule_tree.heading("rule", text="Rule")
        self.rule_tree.pack(side="left", fill="both", expand=True)
        buttons = ttk.Frame(frame)
        buttons.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(buttons, text="Add rule…", command=self._add_rule).pack(fill="x")
        ttk.Button(buttons, text="Edit…", command=self._edit_rule).pack(fill="x", pady=(5, 0))
        ttk.Button(buttons, text="Remove", command=self._remove_rule).pack(fill="x", pady=(5, 0))

    def _build_advanced_tab(self) -> None:
        parent = self.advanced_tab.content
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="Advanced settings", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )
        self.advanced_vars: dict[tuple[str, str], tk.Variable] = {}
        sections = [
            (
                "default_rules",
                "Default scheduling rules",
                [
                    ("max_consecutive_days", "Maximum consecutive workdays", "int"),
                    ("rolling_window_days", "Rolling window length", "int"),
                    ("max_shifts_in_rolling_window", "Maximum shifts in rolling window", "int"),
                    ("max_overnights", "Default maximum O/N shifts", "int"),
                    ("max_weekend_shifts", "Default hard maximum weekend shifts", "int"),
                    ("forbid_86_after_88", "Forbid 8-6 immediately after 8-8", "bool"),
                    ("recovery_after_212_days", "Recovery days after a 2-12 block", "int"),
                ],
            ),
            (
                "quality_weights",
                "Quality preferences",
                [
                    ("hour_balance", "Hour-balance weight", "int"),
                    ("weekend_single", "Split-weekend penalty", "int"),
                    ("isolated_workday", "Isolated-workday penalty", "int"),
                    ("shift_88_singleton", "Single 8-8 block penalty", "int"),
                    ("shift_88_triple", "Three-day 8-8 block penalty", "int"),
                ],
            ),
            (
                "solver",
                "Solver",
                [
                    ("max_time_per_phase_seconds", "Maximum seconds per phase", "float"),
                    ("workers", "Worker threads", "int"),
                    ("random_seed", "Random seed", "int"),
                    ("log_progress", "Detailed solver logging", "bool"),
                ],
            ),
        ]
        row = 1
        for section, title, fields in sections:
            frame = ttk.LabelFrame(parent, text=title, padding=10)
            frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=7)
            frame.columnconfigure(1, weight=1)
            for field_row, (key, label, kind) in enumerate(fields):
                ttk.Label(frame, text=label).grid(row=field_row, column=0, sticky="w", padx=(0, 12), pady=4)
                if kind == "bool":
                    variable: tk.Variable = tk.BooleanVar()
                    widget = ttk.Checkbutton(frame, variable=variable)
                else:
                    variable = tk.StringVar()
                    widget = ttk.Entry(frame, textvariable=variable, width=18)
                widget.grid(row=field_row, column=1, sticky="w", pady=4)
                self.advanced_vars[section, key] = variable
            row += 1

    def _build_run_tab(self) -> None:
        ttk.Label(self.run_tab, text="Validate and generate", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            self.run_tab,
            text=(
                "Validation checks the workbook dates, configuration, doctor rules, and protected assignments. "
                "Generation can take several minutes; the app remains responsive while the optimizer runs."
            ),
            wraplength=850,
            foreground="#4b5563",
        ).pack(anchor="w", pady=(6, 12))
        actions = ttk.Frame(self.run_tab)
        actions.pack(fill="x")
        self.validate_button = ttk.Button(actions, text="Validate inputs", command=lambda: self._start_task("validate"))
        self.validate_button.pack(side="left")
        self.generate_button = ttk.Button(
            actions,
            text="Generate schedule",
            style="Primary.TButton",
            command=lambda: self._start_task("build"),
        )
        self.generate_button.pack(side="left", padx=(8, 0))
        self.stop_button = ttk.Button(
            actions,
            text="Stop",
            command=self._stop_task,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=(8, 0))
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=240)
        self.progress.pack(side="right")
        self.elapsed_var = tk.StringVar(value="Elapsed: 00:00:00")
        ttk.Label(actions, textvariable=self.elapsed_var).pack(side="right", padx=(0, 12))
        self.log = tk.Text(self.run_tab, height=24, wrap="word", font=("Consolas", 10), state="disabled")
        self.log.pack(fill="both", expand=True, pady=(12, 0))

    def _load_initial_config(self) -> None:
        SCHEDULES_DIR.mkdir(exist_ok=True)
        candidates = sorted(SCHEDULES_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        if candidates:
            self._load_config_file(candidates[0], quiet=True)

    def _new_config(self) -> None:
        if not messagebox.askyesno("New configuration", "Start a new configuration? Unsaved changes will be lost."):
            return
        self.config_path = None
        self._load_config_into_ui(new_config())

    def _open_config(self) -> None:
        path = filedialog.askopenfilename(
            title="Open schedule configuration",
            initialdir=SCHEDULES_DIR,
            filetypes=[("JSON configuration", "*.json"), ("All files", "*.*")],
        )
        if path:
            self._load_config_file(Path(path))

    def _load_config_file(self, path: Path, quiet: bool = False) -> None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.config_path = path
            self._load_config_into_ui(normalize_config(raw))
            self._guess_history()
            if not quiet:
                self._log(f"Opened configuration: {path}\n")
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            messagebox.showerror("Could not open configuration", str(exc), parent=self.root)

    def _load_config_into_ui(self, config: dict[str, Any]) -> None:
        self.config = normalize_config(copy.deepcopy(config))
        schedule = self.config["schedule"]
        self.start_var.set(schedule.get("start", ""))
        self.end_var.set(schedule.get("end", ""))
        self.base_hours_var.set(str(self.config.get("base_target_hours", 120)))
        self.prorate_var.set(str(self.config.get("prorate_after_unavailable_days", 3)))
        if not self.output_var.get():
            self.output_var.set(str(SCHEDULES_DIR / suggested_output_name(self.config)))
        for (section, key), variable in self.advanced_vars.items():
            variable.set(self.config[section][key])
        self.current_doctor_index = None
        self._refresh_doctor_tree(select=0 if self.config["doctors"] else None)
        self._update_title()

    def _collect_config(self) -> dict[str, Any]:
        self._save_current_doctor()
        config = copy.deepcopy(self.config)
        config["schedule"] = {"start": self.start_var.get().strip(), "end": self.end_var.get().strip()}
        config["base_target_hours"] = int(self.base_hours_var.get())
        config["prorate_after_unavailable_days"] = int(self.prorate_var.get())
        for (section, key), variable in self.advanced_vars.items():
            current = config[section].get(key)
            if isinstance(variable, tk.BooleanVar):
                value: Any = bool(variable.get())
            elif isinstance(current, float) or (section, key) == (
                "solver",
                "max_time_per_phase_seconds",
            ):
                value = float(variable.get())
            else:
                value = int(variable.get())
            config[section][key] = value
        self.config = config
        return config

    def _save_config(self, save_as: bool = False) -> bool:
        try:
            config = self._collect_config()
        except ValueError as exc:
            messagebox.showerror("Invalid value", f"A numeric field is invalid: {exc}", parent=self.root)
            return False
        path = self.config_path
        if save_as or path is None:
            chosen = filedialog.asksaveasfilename(
                title="Save schedule configuration",
                initialdir=SCHEDULES_DIR,
                initialfile=(path.name if path else "schedule_config.json"),
                defaultextension=".json",
                filetypes=[("JSON configuration", "*.json")],
            )
            if not chosen:
                return False
            path = Path(chosen)
        try:
            path.write_text(json.dumps(config, indent=2), encoding="utf-8")
            self.config_path = path
            self._update_title()
            self._log(f"Saved configuration: {path}\n")
            return True
        except OSError as exc:
            messagebox.showerror("Could not save", str(exc), parent=self.root)
            return False

    def _update_title(self) -> None:
        suffix = self.config_path.name if self.config_path else "Unsaved configuration"
        self.root.title(f"Doctor Schedule Builder — {suffix}")

    def _guess_history(self) -> None:
        try:
            expected_end = parse_date(self.start_var.get()) - timedelta(days=1)
        except ValueError:
            return
        for candidate in sorted(SCHEDULES_DIR.glob("*.xlsx"), key=lambda path: path.stat().st_mtime, reverse=True):
            try:
                read_history_workbook(candidate, expected_end)
                self.history_var.set(str(candidate))
                break
            except ScheduleBuilderError:
                continue
        if not self.output_var.get() or Path(self.output_var.get()).parent == SCHEDULES_DIR:
            self.output_var.set(str(SCHEDULES_DIR / suggested_output_name(self.config)))

    def _browse_history(self) -> None:
        path = filedialog.askopenfilename(
            title="Select previous schedule workbook",
            initialdir=SCHEDULES_DIR,
            filetypes=[("Excel workbook", "*.xlsx")],
        )
        if path:
            self.history_var.set(path)

    def _browse_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Choose output workbook",
            initialdir=SCHEDULES_DIR,
            initialfile=suggested_output_name(self.config),
            defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx")],
        )
        if path:
            self.output_var.set(path)

    def _refresh_doctor_tree(self, select: int | None = None) -> None:
        self._loading_doctor = True
        for item in self.doctor_tree.get_children():
            self.doctor_tree.delete(item)
        for index, doctor in enumerate(self.config.get("doctors", [])):
            self.doctor_tree.insert(
                "",
                "end",
                iid=str(index),
                text=doctor.get("name", "Unnamed"),
                values=(doctor.get("mode", "default").title(), "Yes" if doctor.get("overnight_capable") else "No"),
            )
        self._loading_doctor = False
        if select is not None and 0 <= select < len(self.config.get("doctors", [])):
            self.doctor_tree.selection_set(str(select))
            self.doctor_tree.focus(str(select))
            self._load_doctor(select)

    def _doctor_selected(self, _event=None) -> None:
        if self._loading_doctor:
            return
        selection = self.doctor_tree.selection()
        if not selection:
            return
        new_index = int(selection[0])
        if self.current_doctor_index != new_index:
            self._save_current_doctor()
            self._load_doctor(new_index)

    def _load_doctor(self, index: int) -> None:
        doctors = self.config.get("doctors", [])
        if not 0 <= index < len(doctors):
            return
        self.current_doctor_index = index
        doctor = doctors[index]
        self.doctor_name_var.set(doctor.get("name", ""))
        self.doctor_mode_var.set(doctor.get("mode", "default"))
        self.doctor_overnight_var.set(bool(doctor.get("overnight_capable", False)))
        self.doctor_target_var.set("" if doctor.get("target_hours") is None else str(doctor["target_hours"]))
        self.doctor_defaults_var.set(bool(doctor.get("use_default_rest_rules", True)))
        self.doctor_weekends_var.set(
            "" if doctor.get("max_weekend_shifts") is None else str(doctor["max_weekend_shifts"])
        )
        self.current_time_off = copy.deepcopy(doctor.get("time_off", []))
        self.current_assignments = copy.deepcopy(doctor.get("assignments", {}))
        self.current_rules = copy.deepcopy(doctor.get("rules", []))
        self._refresh_time_off_tree()
        self._refresh_assignment_tree()
        self._refresh_rule_tree()

    def _save_current_doctor(self) -> None:
        index = self.current_doctor_index
        doctors = self.config.get("doctors", [])
        if index is None or not 0 <= index < len(doctors):
            return
        doctor = doctors[index]
        doctor["name"] = self.doctor_name_var.get().strip() or "Unnamed Doctor"
        doctor["mode"] = self.doctor_mode_var.get()
        doctor["overnight_capable"] = bool(self.doctor_overnight_var.get())
        doctor["use_default_rest_rules"] = bool(self.doctor_defaults_var.get())
        target = self.doctor_target_var.get().strip()
        if target:
            doctor["target_hours"] = int(target)
        else:
            doctor.pop("target_hours", None)
        weekends = self.doctor_weekends_var.get().strip()
        if weekends:
            doctor["max_weekend_shifts"] = int(weekends)
        else:
            doctor.pop("max_weekend_shifts", None)
        doctor["time_off"] = copy.deepcopy(self.current_time_off)
        doctor["assignments"] = dict(sorted(self.current_assignments.items()))
        doctor["rules"] = copy.deepcopy(self.current_rules)

    def _apply_doctor_changes(self) -> None:
        try:
            index = self.current_doctor_index
            self._save_current_doctor()
            self._refresh_doctor_tree(select=index)
        except ValueError as exc:
            messagebox.showerror("Invalid doctor value", str(exc), parent=self.root)

    def _add_doctor(self) -> None:
        self._save_current_doctor()
        names = {doctor.get("name", "") for doctor in self.config["doctors"]}
        number = 1
        name = "New Doctor"
        while name in names:
            number += 1
            name = f"New Doctor {number}"
        self.config["doctors"].append(new_doctor(name))
        self._refresh_doctor_tree(select=len(self.config["doctors"]) - 1)

    def _duplicate_doctor(self) -> None:
        if self.current_doctor_index is None:
            return
        self._save_current_doctor()
        duplicate = copy.deepcopy(self.config["doctors"][self.current_doctor_index])
        duplicate["name"] = f"{duplicate.get('name', 'Doctor')} Copy"
        self.config["doctors"].append(duplicate)
        self._refresh_doctor_tree(select=len(self.config["doctors"]) - 1)

    def _delete_doctor(self) -> None:
        index = self.current_doctor_index
        if index is None:
            return
        name = self.config["doctors"][index].get("name", "this doctor")
        if not messagebox.askyesno("Delete doctor", f"Delete {name}?", parent=self.root):
            return
        self.config["doctors"].pop(index)
        self.current_doctor_index = None
        self._refresh_doctor_tree(select=min(index, len(self.config["doctors"]) - 1))

    def _add_time_off(self) -> None:
        initial = parse_date(self.start_var.get())
        dialog = CalendarDialog(
            self.root,
            "Add vacation or time off",
            start=initial,
            end=initial,
            allow_range=True,
        )
        self.root.wait_window(dialog)
        if dialog.result:
            start, end = dialog.result
            self.current_time_off.append(
                start.isoformat() if start == end else {"start": start.isoformat(), "end": end.isoformat()}
            )
            self._refresh_time_off_tree()

    def _edit_time_off(self) -> None:
        selection = self.time_off_tree.selection()
        if not selection:
            return
        index = int(selection[0])
        item = self.current_time_off[index]
        start = parse_date(item.get("start")) if isinstance(item, dict) else parse_date(str(item))
        end = parse_date(item.get("end")) if isinstance(item, dict) else start
        dialog = CalendarDialog(self.root, "Edit vacation or time off", start=start, end=end, allow_range=True)
        self.root.wait_window(dialog)
        if dialog.result:
            start, end = dialog.result
            self.current_time_off[index] = (
                start.isoformat() if start == end else {"start": start.isoformat(), "end": end.isoformat()}
            )
            self._refresh_time_off_tree()

    def _remove_time_off(self) -> None:
        selected = [int(item) for item in self.time_off_tree.selection()]
        for index in sorted(selected, reverse=True):
            self.current_time_off.pop(index)
        self._refresh_time_off_tree()

    def _refresh_time_off_tree(self) -> None:
        for item in self.time_off_tree.get_children():
            self.time_off_tree.delete(item)
        for index, value in enumerate(self.current_time_off):
            self.time_off_tree.insert("", "end", iid=str(index), values=(time_off_label(value),))

    def _add_assignment(self) -> None:
        dialog = AssignmentDialog(self.root, initial_date=parse_date(self.start_var.get()))
        self.root.wait_window(dialog)
        if dialog.result:
            self._store_assignment_range(*dialog.result)

    def _edit_assignment(self) -> None:
        selection = self.assignment_tree.selection()
        if not selection:
            return
        original_date = selection[0]
        dialog = AssignmentDialog(
            self.root,
            initial_date=parse_date(original_date),
            initial_shift=self.current_assignments[original_date],
        )
        self.root.wait_window(dialog)
        if dialog.result:
            self.current_assignments.pop(original_date, None)
            self._store_assignment_range(*dialog.result)

    def _store_assignment_range(self, start: date, end: date, shift: str) -> None:
        for offset in range((end - start).days + 1):
            self.current_assignments[(start + timedelta(days=offset)).isoformat()] = shift
        self._refresh_assignment_tree()

    def _remove_assignment(self) -> None:
        for value in self.assignment_tree.selection():
            self.current_assignments.pop(value, None)
        self._refresh_assignment_tree()

    def _refresh_assignment_tree(self) -> None:
        for item in self.assignment_tree.get_children():
            self.assignment_tree.delete(item)
        for day, shift in sorted(self.current_assignments.items()):
            self.assignment_tree.insert("", "end", iid=day, values=(day, shift))

    def _add_rule(self) -> None:
        dialog = RuleDialog(self.root)
        self.root.wait_window(dialog)
        if dialog.result:
            self.current_rules.append(dialog.result)
            self._refresh_rule_tree()

    def _edit_rule(self) -> None:
        selection = self.rule_tree.selection()
        if not selection:
            return
        index = int(selection[0])
        dialog = RuleDialog(self.root, self.current_rules[index])
        self.root.wait_window(dialog)
        if dialog.result:
            self.current_rules[index] = dialog.result
            self._refresh_rule_tree()

    def _remove_rule(self) -> None:
        selected = [int(item) for item in self.rule_tree.selection()]
        for index in sorted(selected, reverse=True):
            self.current_rules.pop(index)
        self._refresh_rule_tree()

    def _refresh_rule_tree(self) -> None:
        for item in self.rule_tree.get_children():
            self.rule_tree.delete(item)
        for index, rule in enumerate(self.current_rules):
            self.rule_tree.insert("", "end", iid=str(index), values=(rule_label(rule),))

    def _start_task(self, task: str) -> None:
        if self._busy:
            return
        try:
            config = self._collect_config()
        except ValueError as exc:
            messagebox.showerror("Invalid value", f"A numeric field is invalid: {exc}", parent=self.root)
            return
        history = Path(self.history_var.get().strip())
        output = Path(self.output_var.get().strip())
        if not history.is_file():
            messagebox.showerror("History workbook required", "Select an existing history workbook.", parent=self.root)
            self.notebook.select(self.setup_tab)
            return
        if task == "build" and output.exists() and not messagebox.askyesno(
            "Replace workbook?", f"{output.name} already exists. Replace it?", parent=self.root
        ):
            return
        self._active_task = task
        self._task_cancel_event = threading.Event()
        self._task_started_at = time.monotonic()
        self._set_busy(True)
        self.notebook.select(self.run_tab)
        self._log(f"\n{task.title()} started…\n")
        thread = threading.Thread(
            target=self._task_worker,
            args=(task, config, history, output, self._task_cancel_event),
            daemon=True,
        )
        thread.start()
        self.root.after(100, self._poll_task)

    def _task_worker(
        self,
        task: str,
        config: dict[str, Any],
        history: Path,
        output: Path,
        cancel_event: threading.Event,
    ) -> None:
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as handle:
                json.dump(config, handle, indent=2)
                temp_path = Path(handle.name)
            if task == "validate":
                loaded, imported = self.service.validate(history, temp_path)
                result: Any = (
                    f"Valid: {len(loaded.doctors)} doctors, {len(loaded.dates)} schedule days, "
                    f"{len(imported.dates)} history days ({imported.dates[0]} to {imported.dates[-1]})."
                )
            else:
                result = self.service.build(
                    history,
                    temp_path,
                    output,
                    cancel_event=cancel_event,
                )
            self._task_queue.put(("success", task, result))
        except ScheduleCancelledError as exc:
            self._task_queue.put(("cancelled", task, exc))
        except Exception as exc:
            self._task_queue.put(("error", task, exc))
        finally:
            if temp_path:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _poll_task(self) -> None:
        self._update_elapsed()
        try:
            status, task, result = self._task_queue.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_task)
            return
        self._set_busy(False)
        self._active_task = None
        self._task_cancel_event = None
        self._task_started_at = None
        if status == "cancelled":
            elapsed = self.elapsed_var.get().removeprefix("Elapsed: ")
            self._log(f"Generation stopped after {elapsed}.\n")
            messagebox.showinfo(
                "Generation stopped",
                "Schedule generation was stopped. No new workbook was written.",
                parent=self.root,
            )
            return
        if status == "error":
            message = str(result)
            self._log(f"Error: {message}\n")
            title = "Validation failed" if isinstance(result, ScheduleBuilderError) else "Unexpected error"
            messagebox.showerror(title, message, parent=self.root)
            return
        if task == "validate":
            self._log(f"{result}\n")
            messagebox.showinfo("Inputs are valid", result, parent=self.root)
        else:
            outcome = result
            open_count = sum(len(shifts) for shifts in outcome.result.open_shifts.values())
            self._log(f"Schedule written to: {outcome.output_path}\n")
            self._log(f"Automatic OPEN shifts: {open_count}\n")
            for report in outcome.result.phase_reports:
                self._log(
                    f"  {report.name}: {report.status}, objective={report.objective:g}, "
                    f"time={report.wall_time_seconds:.2f}s\n"
                )
            messagebox.showinfo("Schedule complete", f"Schedule written to:\n{outcome.output_path}", parent=self.root)

    def _stop_task(self) -> None:
        if not self._busy or self._active_task != "build" or self._task_cancel_event is None:
            return
        self._task_cancel_event.set()
        self.stop_button.configure(text="Stopping...", state="disabled")
        self._log("Stop requested; waiting for the optimizer to stop...\n")

    def _update_elapsed(self) -> None:
        if self._task_started_at is not None:
            elapsed = time.monotonic() - self._task_started_at
            self.elapsed_var.set(f"Elapsed: {format_elapsed(elapsed)}")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.validate_button.configure(state=state)
        self.generate_button.configure(state=state)
        if busy:
            self.elapsed_var.set("Elapsed: 00:00:00")
            self.stop_button.configure(
                text="Stop",
                state="normal" if self._active_task == "build" else "disabled",
            )
            self.progress.start(12)
        else:
            self.stop_button.configure(text="Stop", state="disabled")
            self.progress.stop()

    def _log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message)
        self.log.see("end")
        self.log.configure(state="disabled")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--smoke-test", action="store_true")
    args, _unknown = parser.parse_known_args(argv)
    root = tk.Tk()
    if args.smoke_test:
        root.withdraw()
    ScheduleBuilderApp(root)
    if args.smoke_test:
        root.update_idletasks()
        root.destroy()
        return 0
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
