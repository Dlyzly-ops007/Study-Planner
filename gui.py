"""
Tkinter desktop interface for the Study Planner application.

This module only handles presentation and user interaction. All data
rules (validation, persistence, ID assignment, filtering/sorting/deadline
classification, study-time and progress calculations) live in
data_manager.py -- no tab computes those itself, they all call into
DataManager so the logic is defined exactly once.

Layout: a File menu (export/backup/restore), a reminder banner, and a
ttk.Notebook with seven tabs -- Tasks (search/filter/sort table), Upcoming
(overdue + due-this-week), Calendar (month view + day detail), Study
Sessions (log + history of study time), Analytics (dashboard + charts +
subject stats), Subjects (CRUD + per-subject progress), and Settings.
Phase 4 additions live in gui_tools.py.
"""

from __future__ import annotations

import calendar
import os
import tkinter as tk
import traceback
from datetime import date
from tkinter import ttk, messagebox
from typing import Callable, Optional

try:
    import warnings

    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    HAS_MATPLOTLIB = True
    # Harmless at very small window sizes (labels just overlap a bit);
    # not worth a console warning on every resize.
    warnings.filterwarnings("ignore", message="Tight layout not applied")
except ImportError:  # charts are optional; everything else still works
    HAS_MATPLOTLIB = False

import reminders
from data_manager import DataManager, StorageError, SubjectHasTasksError, ValidationError
from gui_tools import FileActions, ReminderController, SettingsTab
from models import DATE_FORMAT, PRIORITIES, STATUSES, Subject, StudySession, Task
from settings import Settings

# Errors whose message is written for the user: show it, keep the app running.
USER_ERRORS = (ValidationError, StorageError)

DEADLINE_FILTERS = ("All", "Today", "This week", "Overdue", "Upcoming")
STATUS_FILTER_OPTIONS = ("All",) + STATUSES
PRIORITY_FILTER_OPTIONS = ("All",) + PRIORITIES
PERIOD_OPTIONS = ("Today", "This week", "This month", "All time")

# Treeview column id -> DataManager sort_by key, for clickable headers.
SORT_COLUMNS = {
    "title": "title",
    "subject": "subject",
    "priority": "priority",
    "deadline": "deadline",
    "status": "status",
}

# Deadline-category -> row text color, applied to the Tasks and Upcoming
# tables so overdue/due-soon items stand out without extra UI clutter.
ROW_TAG_COLORS = {
    "overdue": "#c0392b",
    "today": "#b8860b",
    "tomorrow": "#1a5276",
}


class StudyPlannerApp:
    """Top-level application: owns the main window and the six tabs."""

    def __init__(self, root: tk.Tk, data_manager: DataManager, settings: Settings):
        self.root = root
        self.dm = data_manager
        self.settings = settings
        self.data_dir = os.path.dirname(os.path.abspath(data_manager.filepath))

        self.root.title("Study Planner")
        self.root.geometry("1100x740")
        self.root.minsize(820, 580)
        self.root.report_callback_exception = self._report_unexpected_error

        menubar = tk.Menu(self.root)
        FileActions(self).build_menu(menubar)
        self.root.configure(menu=menubar)

        header = ttk.Label(self.root, text="Study Planner", font=("Segoe UI", 16, "bold"))
        header.pack(pady=(10, 2))
        self.reminders = ReminderController(self, self.root)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.tasks_tab = TasksTab(self.notebook, self)
        self.upcoming_tab = UpcomingTab(self.notebook, self)
        self.calendar_tab = CalendarTab(self.notebook, self)
        self.sessions_tab = StudySessionsTab(self.notebook, self)
        self.analytics_tab = AnalyticsTab(self.notebook, self)
        self.subjects_tab = SubjectsTab(self.notebook, self)
        self.settings_tab = SettingsTab(self.notebook, self)

        self.notebook.add(self.tasks_tab, text="Tasks")
        self.notebook.add(self.upcoming_tab, text="Upcoming")
        self.notebook.add(self.calendar_tab, text="Calendar")
        self.notebook.add(self.sessions_tab, text="Study Sessions")
        self.notebook.add(self.analytics_tab, text="Analytics")
        self.notebook.add(self.subjects_tab, text="Subjects")
        self.notebook.add(self.settings_tab, text="Settings")

        # A tab is refreshed whenever it becomes visible (this event also
        # fires for the first tab at startup), so data changed on one tab
        # always shows up on the next.
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        if self.dm.startup_warning:
            messagebox.showwarning("Study Planner", self.dm.startup_warning)
        # Deferred so the main window is drawn before any reminder popup.
        self.root.after(500, self.reminders.check_and_notify)

    def _report_unexpected_error(self, exc_type, exc_value, exc_tb) -> None:
        # A programming bug: keep the full traceback on the console for
        # debugging, but tell the user instead of failing silently.
        traceback.print_exception(exc_type, exc_value, exc_tb)
        messagebox.showerror("Unexpected error", f"Something went wrong:\n{exc_value}")

    def _on_tab_changed(self, _event=None) -> None:
        self.current_tab().refresh()

    def current_tab(self):
        return self.notebook.nametowidget(self.notebook.select())

    def show_tab(self, tab: ttk.Frame) -> None:
        self.notebook.select(tab)

    def notify_data_changed(self) -> None:
        """Called by any tab after it adds/edits/deletes data. Only the
        visible tab is redrawn now; every other tab refreshes itself when
        it's next shown, so hidden charts aren't regenerated needlessly."""
        self.current_tab().refresh()
        self.reminders.refresh_banner()


class TasksTab(ttk.Frame):
    """Search, filter, sort, and manage the full task list."""

    def __init__(self, parent: ttk.Notebook, app: StudyPlannerApp):
        super().__init__(parent)
        self.app = app
        self.dm = app.dm

        self._selected_task_id: Optional[int] = None
        self._sort_by = "deadline"
        self._sort_reverse = False
        self._subject_filter_ids: dict[str, Optional[int]] = {"All": None}
        self._refreshing = False

        self._build()

    # -- layout ---------------------------------------------------------

    def _build(self) -> None:
        search_bar = ttk.Frame(self)
        search_bar.pack(fill="x", padx=6, pady=(8, 2))
        ttk.Label(search_bar, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        ttk.Entry(search_bar, textvariable=self.search_var).pack(
            side="left", fill="x", expand=True, padx=(4, 12)
        )
        self.search_var.trace_add("write", lambda *_: self.refresh())
        ttk.Button(search_bar, text="Clear Filters", command=self._clear_filters).pack(side="left")

        filter_bar = ttk.Frame(self)
        filter_bar.pack(fill="x", padx=6, pady=(2, 2))

        ttk.Label(filter_bar, text="Status:").pack(side="left")
        self.status_var = tk.StringVar(value="All")
        ttk.Combobox(
            filter_bar, textvariable=self.status_var, values=STATUS_FILTER_OPTIONS,
            state="readonly", width=12,
        ).pack(side="left", padx=(4, 12))
        self.status_var.trace_add("write", lambda *_: self.refresh())

        ttk.Label(filter_bar, text="Priority:").pack(side="left")
        self.priority_var = tk.StringVar(value="All")
        ttk.Combobox(
            filter_bar, textvariable=self.priority_var, values=PRIORITY_FILTER_OPTIONS,
            state="readonly", width=9,
        ).pack(side="left", padx=(4, 12))
        self.priority_var.trace_add("write", lambda *_: self.refresh())

        ttk.Label(filter_bar, text="Subject:").pack(side="left")
        self.subject_var = tk.StringVar(value="All")
        self.subject_combo = ttk.Combobox(
            filter_bar, textvariable=self.subject_var, values=["All"], state="readonly", width=14,
        )
        self.subject_combo.pack(side="left", padx=(4, 12))
        self.subject_var.trace_add("write", lambda *_: self.refresh())

        ttk.Label(filter_bar, text="Deadline:").pack(side="left")
        self.deadline_var = tk.StringVar(value="All")
        ttk.Combobox(
            filter_bar, textvariable=self.deadline_var, values=DEADLINE_FILTERS,
            state="readonly", width=11,
        ).pack(side="left", padx=(4, 12))
        self.deadline_var.trace_add("write", lambda *_: self.refresh())

        self.summary_var = tk.StringVar()
        ttk.Label(self, textvariable=self.summary_var, font=("Segoe UI", 9, "bold")).pack(
            anchor="w", padx=8, pady=(6, 0)
        )
        self.count_var = tk.StringVar()
        ttk.Label(self, textvariable=self.count_var, font=("Segoe UI", 9)).pack(anchor="w", padx=8)

        table_frame = ttk.Frame(self)
        table_frame.pack(fill="both", expand=True, padx=6, pady=6)
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        columns = ("title", "subject", "priority", "deadline", "status")
        headings = {
            "title": "Task", "subject": "Subject", "priority": "Priority",
            "deadline": "Deadline", "status": "Status",
        }
        widths = {"title": 240, "subject": 130, "priority": 80, "deadline": 100, "status": 120}

        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        for col in columns:
            self.tree.heading(col, text=headings[col], command=lambda c=col: self._on_sort(c))
            self.tree.column(col, width=widths[col], minwidth=60, anchor="w", stretch=(col == "title"))
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Double-1>", lambda _e: self.open_edit())

        for tag, color in ROW_TAG_COLORS.items():
            self.tree.tag_configure(tag, foreground=color)

        vscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        vscroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=vscroll.set)

        btn_row = ttk.Frame(self)
        btn_row.pack(pady=(0, 8))
        ttk.Button(btn_row, text="Add Task", command=self.open_add).pack(side="left", padx=3)
        ttk.Button(btn_row, text="Edit Task", command=self.open_edit).pack(side="left", padx=3)
        ttk.Button(btn_row, text="Delete Task", command=self.delete_selected).pack(side="left", padx=3)
        ttk.Label(btn_row, text="  Double-click to edit; right-click to change status.",
                  foreground="#777777").pack(side="left")

        self.context_menu = tk.Menu(self, tearoff=0)
        for status in STATUSES:
            self.context_menu.add_command(
                label=f"Mark {status}", command=lambda s=status: self._quick_status(s)
            )

    # -- filter bar behavior ---------------------------------------------

    def _clear_filters(self) -> None:
        # Each .set() would trigger a refresh; batch them into one, and
        # restore the default sort too so "clear" means the default view.
        self._refreshing = True
        try:
            self.search_var.set("")
            self.status_var.set("All")
            self.priority_var.set("All")
            self.subject_var.set("All")
            self.deadline_var.set("All")
            self._sort_by, self._sort_reverse = "deadline", False
        finally:
            self._refreshing = False
        self.refresh()

    def _on_sort(self, column: str) -> None:
        sort_by = SORT_COLUMNS.get(column, "deadline")
        if self._sort_by == sort_by:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_by = sort_by
            self._sort_reverse = False
        self.refresh()

    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        self._selected_task_id = int(selection[0]) if selection else None

    def _on_right_click(self, event) -> None:
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        self.tree.selection_set(row_id)
        self._on_select()
        self.context_menu.tk_popup(event.x_root, event.y_root)

    def _quick_status(self, status: str) -> None:
        if self._selected_task_id is None:
            return
        try:
            self.dm.set_task_status(self._selected_task_id, status)
        except USER_ERRORS as exc:
            messagebox.showerror("Cannot update status", str(exc))
            return
        self.app.notify_data_changed()

    # -- refresh ----------------------------------------------------------

    def refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        try:
            self._do_refresh()
        finally:
            self._refreshing = False

    def _do_refresh(self) -> None:
        subjects = self.dm.get_subjects()
        names = ["All"] + [s.name for s in subjects]
        self._subject_filter_ids = {"All": None}
        for s in subjects:
            self._subject_filter_ids[s.name] = s.id
        self.subject_combo.configure(values=names)
        if self.subject_var.get() not in names:
            self.subject_var.set("All")

        subject_id = self._subject_filter_ids.get(self.subject_var.get())

        filtered = self.dm.get_tasks_filtered(
            search=self.search_var.get(),
            status=self.status_var.get(),
            priority=self.priority_var.get(),
            subject_id=subject_id,
            deadline_filter=self.deadline_var.get(),
            sort_by=self._sort_by,
            reverse=self._sort_reverse,
        )

        for row in self.tree.get_children():
            self.tree.delete(row)

        today = date.today()
        for task in filtered:
            subject = self.dm.get_subject(task.subject_id)
            subject_name = subject.name if subject else "(deleted)"
            category = self.dm.deadline_category(task, today)
            tags = (category,) if category in ROW_TAG_COLORS else ()
            self.tree.insert(
                "", tk.END, iid=str(task.id), tags=tags,
                values=(task.title, subject_name, task.priority, task.deadline, task.status),
            )

        total = len(self.dm.get_tasks())
        shown = len(filtered)
        if shown == 0:
            self.count_var.set(
                "No tasks match the current filters." if total else "No tasks have been added yet."
            )
        else:
            self.count_var.set(f"Showing {shown} of {total} tasks")

        summary = self.dm.get_summary()
        self.summary_var.set(
            f"Total: {summary['total']}   Completed: {summary['completed']}   "
            f"In Progress: {summary['in_progress']}   Not Started: {summary['not_started']}   "
            f"Overdue: {summary['overdue']}   Due Today: {summary['due_today']}"
        )
        self._selected_task_id = None

    # -- CRUD ---------------------------------------------------------------

    def open_add(self) -> None:
        if not self.dm.get_subjects():
            messagebox.showinfo("Add Task", "Add a subject first (on the Subjects tab).")
            return
        TaskDialog(
            self, title="Add Task", subjects=self.dm.get_subjects(), on_submit=self._add_task,
            default_priority=self.app.settings.get("default_priority"),
            default_status=self.app.settings.get("default_status"),
        )

    def _add_task(self, values: dict) -> bool:
        try:
            self.dm.add_task(**values)
        except USER_ERRORS as exc:
            messagebox.showerror("Cannot add task", str(exc))
            return False
        self.app.notify_data_changed()
        return True

    def open_edit(self) -> None:
        if self._selected_task_id is None:
            messagebox.showinfo("Edit Task", "Select a task first.")
            return
        task = self.dm.get_task(self._selected_task_id)
        TaskDialog(
            self, title="Edit Task", subjects=self.dm.get_subjects(),
            on_submit=lambda values: self._edit_task(task.id, values), initial_task=task,
            study_minutes=self.dm.get_task_study_minutes(task.id),
            format_duration=self.dm.format_duration,
        )

    def _edit_task(self, task_id: int, values: dict) -> bool:
        try:
            self.dm.edit_task(task_id, **values)
        except USER_ERRORS as exc:
            messagebox.showerror("Cannot edit task", str(exc))
            return False
        self.app.notify_data_changed()
        return True

    def delete_selected(self) -> None:
        if self._selected_task_id is None:
            messagebox.showinfo("Delete Task", "Select a task first.")
            return
        task = self.dm.get_task(self._selected_task_id)
        if not messagebox.askyesno(
            "Delete Task", f"Delete task '{task.title}'?\n\nStudy sessions logged for it are kept."
        ):
            return
        try:
            self.dm.delete_task(task.id)
        except USER_ERRORS as exc:
            messagebox.showerror("Cannot delete task", str(exc))
            return
        self.app.notify_data_changed()


class UpcomingTab(ttk.Frame):
    """Read-only view: overdue tasks, then tasks due in the next 7 days."""

    def __init__(self, parent: ttk.Notebook, app: StudyPlannerApp):
        super().__init__(parent)
        self.app = app
        self.dm = app.dm
        self._build()

    def _build(self) -> None:
        ttk.Label(self, text="Overdue", font=("Segoe UI", 11, "bold")).pack(
            anchor="w", padx=8, pady=(10, 2)
        )
        self.overdue_tree = self._make_tree()
        self.overdue_tree.pack(fill="x", padx=8, pady=(0, 12))

        ttk.Label(self, text="Due in the next 7 days", font=("Segoe UI", 11, "bold")).pack(
            anchor="w", padx=8, pady=(0, 2)
        )
        self.upcoming_tree = self._make_tree()
        self.upcoming_tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _make_tree(self) -> ttk.Treeview:
        columns = ("title", "subject", "priority", "deadline", "status")
        headings = {
            "title": "Task", "subject": "Subject", "priority": "Priority",
            "deadline": "Deadline", "status": "Status",
        }
        tree = ttk.Treeview(self, columns=columns, show="headings", height=6)
        for col in columns:
            tree.heading(col, text=headings[col])
            tree.column(col, width=220 if col == "title" else 110, minwidth=60,
                        anchor="w", stretch=(col == "title"))
        for tag, color in ROW_TAG_COLORS.items():
            tree.tag_configure(tag, foreground=color)
        return tree

    def refresh(self) -> None:
        today = date.today()

        for row in self.overdue_tree.get_children():
            self.overdue_tree.delete(row)
        overdue = self.dm.get_tasks_filtered(
            deadline_filter="Overdue", exclude_completed=True, sort_by="deadline"
        )
        if not overdue:
            self.overdue_tree.insert("", tk.END, values=("No overdue tasks.", "", "", "", ""))
        else:
            for task in overdue:
                subject = self.dm.get_subject(task.subject_id)
                self.overdue_tree.insert(
                    "", tk.END, iid=f"o{task.id}", tags=("overdue",),
                    values=(task.title, subject.name if subject else "(deleted)",
                            task.priority, task.deadline, task.status),
                )

        for row in self.upcoming_tree.get_children():
            self.upcoming_tree.delete(row)
        upcoming = self.dm.get_tasks_filtered(
            deadline_filter="This week", exclude_completed=True, sort_by="deadline"
        )
        if not upcoming:
            self.upcoming_tree.insert(
                "", tk.END, values=("No upcoming deadlines.", "", "", "", "")
            )
        else:
            for task in upcoming:
                subject = self.dm.get_subject(task.subject_id)
                category = self.dm.deadline_category(task, today)
                tags = (category,) if category in ROW_TAG_COLORS else ()
                self.upcoming_tree.insert(
                    "", tk.END, iid=f"u{task.id}", tags=tags,
                    values=(task.title, subject.name if subject else "(deleted)",
                            task.priority, task.deadline, task.status),
                )


class CalendarTab(ttk.Frame):
    """Simple month grid; clicking a date shows that day's tasks below."""

    def __init__(self, parent: ttk.Notebook, app: StudyPlannerApp):
        super().__init__(parent)
        self.app = app
        self.dm = app.dm
        today = date.today()
        self._year = today.year
        self._month = today.month
        self._selected_date: str = today.strftime(DATE_FORMAT)
        self._build()

    def _build(self) -> None:
        nav = ttk.Frame(self)
        nav.pack(fill="x", padx=8, pady=(10, 4))
        ttk.Button(nav, text="< Prev", command=self._prev_month).pack(side="left")
        self.month_label_var = tk.StringVar()
        ttk.Label(nav, textvariable=self.month_label_var, font=("Segoe UI", 12, "bold")).pack(
            side="left", expand=True
        )
        ttk.Button(nav, text="Next >", command=self._next_month).pack(side="right")
        ttk.Button(nav, text="Today", command=self._go_today).pack(side="right", padx=6)

        self.grid_frame = ttk.Frame(self)
        self.grid_frame.pack(padx=8, pady=4)
        ttk.Label(
            self, text="Legend:  12* = task(s) due    [12] = today    >12< = selected day",
            foreground="#555555",
        ).pack()

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=8, pady=8)

        self.selected_date_var = tk.StringVar()
        ttk.Label(self, textvariable=self.selected_date_var, font=("Segoe UI", 10, "bold")).pack(
            anchor="w", padx=8
        )

        columns = ("title", "subject", "priority", "status")
        headings = {"title": "Task", "subject": "Subject", "priority": "Priority", "status": "Status"}
        self.day_tree = ttk.Treeview(self, columns=columns, show="headings", height=8)
        for col in columns:
            self.day_tree.heading(col, text=headings[col])
            self.day_tree.column(col, width=180 if col == "title" else 120, anchor="w")
        self.day_tree.pack(fill="both", expand=True, padx=8, pady=(2, 10))

    def _prev_month(self) -> None:
        self._month -= 1
        if self._month == 0:
            self._month, self._year = 12, self._year - 1
        self.refresh()

    def _next_month(self) -> None:
        self._month += 1
        if self._month == 13:
            self._month, self._year = 1, self._year + 1
        self.refresh()

    def _go_today(self) -> None:
        today = date.today()
        self._year, self._month = today.year, today.month
        self._selected_date = today.strftime(DATE_FORMAT)
        self.refresh()

    def _select_date(self, date_str: str) -> None:
        self._selected_date = date_str
        self._render_day_grid()
        self._render_day_tasks()

    def refresh(self) -> None:
        self.month_label_var.set(f"{calendar.month_name[self._month]} {self._year}")
        self._render_day_grid()
        self._render_day_tasks()

    def _render_day_grid(self) -> None:
        for child in list(self.grid_frame.winfo_children()):
            child.destroy()

        for col, name in enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
            ttk.Label(
                self.grid_frame, text=name, width=7, anchor="center", font=("Segoe UI", 9, "bold")
            ).grid(row=0, column=col, padx=1, pady=1)

        days_with_tasks = self.dm.get_task_days_in_month(self._year, self._month)
        today_str = date.today().strftime(DATE_FORMAT)

        cal = calendar.Calendar(firstweekday=0)  # weeks start Monday
        for r, week in enumerate(cal.monthdayscalendar(self._year, self._month), start=1):
            for c, day in enumerate(week):
                if day == 0:
                    ttk.Label(self.grid_frame, text="", width=7).grid(row=r, column=c, padx=1, pady=1)
                    continue
                date_str = f"{self._year:04d}-{self._month:02d}-{day:02d}"
                label = str(day)
                if day in days_with_tasks:
                    label += "*"
                if date_str == today_str:
                    label = f"[{label}]"
                if date_str == self._selected_date:
                    label = f">{label}<"
                ttk.Button(
                    self.grid_frame, text=label, width=7,
                    command=lambda d=date_str: self._select_date(d),
                ).grid(row=r, column=c, padx=1, pady=1)

    def _render_day_tasks(self) -> None:
        for row in self.day_tree.get_children():
            self.day_tree.delete(row)
        self.selected_date_var.set(f"Tasks on {self._selected_date}")
        tasks = self.dm.get_tasks_by_date(self._selected_date)
        if not tasks:
            self.day_tree.insert("", tk.END, values=("No tasks due on this date.", "", "", ""))
            return
        for task in tasks:
            subject = self.dm.get_subject(task.subject_id)
            self.day_tree.insert(
                "", tk.END, iid=f"d{task.id}",
                values=(task.title, subject.name if subject else "(deleted)", task.priority, task.status),
            )


class SubjectsTab(ttk.Frame):
    """Subject CRUD plus a per-subject task-count detail panel."""

    def __init__(self, parent: ttk.Notebook, app: StudyPlannerApp):
        super().__init__(parent)
        self.app = app
        self.dm = app.dm
        self._selected_subject_id: Optional[int] = None
        self._subject_order: list[Subject] = []
        self._build()

    def _build(self) -> None:
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=8, pady=8)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        list_frame = ttk.LabelFrame(body, text="Subjects")
        list_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        self.listbox = tk.Listbox(list_frame, exportselection=False)
        self.listbox.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        self.listbox.configure(yscrollcommand=scrollbar.set)

        btn_row = ttk.Frame(list_frame)
        btn_row.grid(row=1, column=0, columnspan=2, pady=(0, 6))
        ttk.Button(btn_row, text="Add Subject", command=self.open_add).pack(side="left", padx=3)
        ttk.Button(btn_row, text="Edit Subject", command=self.open_edit).pack(side="left", padx=3)
        ttk.Button(btn_row, text="Delete Subject", command=self.delete_selected).pack(side="left", padx=3)

        detail_frame = ttk.LabelFrame(body, text="Subject Details")
        detail_frame.grid(row=0, column=1, sticky="nsew")

        self.detail_var = tk.StringVar(value="Select a subject to see its details.")
        ttk.Label(detail_frame, textvariable=self.detail_var, justify="left", wraplength=380).pack(
            anchor="nw", padx=10, pady=10
        )

    def refresh(self) -> None:
        self.listbox.delete(0, tk.END)
        self._subject_order = self.dm.get_subjects()
        for subject in self._subject_order:
            stats = self.dm.get_subject_summary(subject.id)
            self.listbox.insert(tk.END, f"{subject.name}  ({stats['completed']}/{stats['total']})")
        self._selected_subject_id = None
        self._render_detail()

    def _on_select(self, _event=None) -> None:
        selection = self.listbox.curselection()
        self._selected_subject_id = self._subject_order[selection[0]].id if selection else None
        self._render_detail()

    def _render_detail(self) -> None:
        subject = (
            self.dm.get_subject(self._selected_subject_id)
            if self._selected_subject_id is not None
            else None
        )
        if subject is None:
            self.detail_var.set(
                "Select a subject to see its details." if self._subject_order
                else "No subjects have been added yet.\nClick 'Add Subject' to create one."
            )
            return
        stats = self.dm.get_subject_summary(subject.id)  # period="All time" by default
        description = subject.description or "(no description)"
        study_time = self.dm.format_duration(stats["study_minutes"])
        self.detail_var.set(
            f"{subject.name}\n\n{description}\n\n"
            f"Total tasks: {stats['total']}\n"
            f"Completed: {stats['completed']}\n"
            f"Pending: {stats['pending']}\n"
            f"Overdue: {stats['overdue']}\n"
            f"Progress: {stats['completion_pct']:.1f}%\n"
            f"Study time: {study_time}"
        )

    def open_add(self) -> None:
        SubjectDialog(self, title="Add Subject", on_submit=self._add_subject)

    def _add_subject(self, name: str, description: str) -> bool:
        try:
            self.dm.add_subject(name, description)
        except USER_ERRORS as exc:
            messagebox.showerror("Cannot add subject", str(exc))
            return False
        self.app.notify_data_changed()
        return True

    def open_edit(self) -> None:
        if self._selected_subject_id is None:
            messagebox.showinfo("Edit Subject", "Select a subject first.")
            return
        subject = self.dm.get_subject(self._selected_subject_id)
        SubjectDialog(
            self, title="Edit Subject",
            on_submit=lambda name, desc: self._edit_subject(subject.id, name, desc),
            initial_name=subject.name, initial_description=subject.description,
        )

    def _edit_subject(self, subject_id: int, name: str, description: str) -> bool:
        try:
            self.dm.edit_subject(subject_id, name, description)
        except USER_ERRORS as exc:
            messagebox.showerror("Cannot edit subject", str(exc))
            return False
        self.app.notify_data_changed()
        return True

    def delete_selected(self) -> None:
        if self._selected_subject_id is None:
            messagebox.showinfo("Delete Subject", "Select a subject first.")
            return
        subject = self.dm.get_subject(self._selected_subject_id)
        if not messagebox.askyesno("Delete Subject", f"Delete subject '{subject.name}'?"):
            return
        try:
            self.dm.delete_subject(subject.id)
        except SubjectHasTasksError as exc:
            parts = []
            if exc.task_count:
                parts.append(f"{exc.task_count} task(s)")
            if exc.session_count:
                parts.append(f"{exc.session_count} study session(s)")
            messagebox.showwarning(
                "Cannot delete subject",
                f"'{exc.subject.name}' still has {' and '.join(parts)} attached.\n"
                "Delete or reassign those first, then try again.",
            )
            return
        except USER_ERRORS as exc:
            messagebox.showerror("Cannot delete subject", str(exc))
            return
        self.app.notify_data_changed()


class StudySessionsTab(ttk.Frame):
    """Log study sessions and browse study history."""

    def __init__(self, parent: ttk.Notebook, app: StudyPlannerApp):
        super().__init__(parent)
        self.app = app
        self.dm = app.dm
        self._selected_session_id: Optional[int] = None
        self._subject_filter_ids: dict[str, Optional[int]] = {"All": None}
        self._build()

    def _build(self) -> None:
        self.summary_var = tk.StringVar()
        ttk.Label(self, textvariable=self.summary_var, font=("Segoe UI", 9, "bold")).pack(
            anchor="w", padx=8, pady=(10, 4)
        )

        filter_bar = ttk.Frame(self)
        filter_bar.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Label(filter_bar, text="Show:").pack(side="left")
        self.period_var = tk.StringVar(value="All time")
        ttk.Combobox(
            filter_bar, textvariable=self.period_var, values=PERIOD_OPTIONS,
            state="readonly", width=12,
        ).pack(side="left", padx=(4, 12))
        self.period_var.trace_add("write", lambda *_: self.refresh())

        ttk.Label(filter_bar, text="Subject:").pack(side="left")
        self.subject_var = tk.StringVar(value="All")
        self.subject_combo = ttk.Combobox(
            filter_bar, textvariable=self.subject_var, values=["All"], state="readonly", width=14,
        )
        self.subject_combo.pack(side="left", padx=(4, 12))
        self.subject_var.trace_add("write", lambda *_: self.refresh())
        ttk.Button(filter_bar, text="Clear Filters", command=self._clear_filters).pack(side="left")

        table_frame = ttk.Frame(self)
        table_frame.pack(fill="both", expand=True, padx=8, pady=4)
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        columns = ("date", "subject", "task", "duration", "notes")
        headings = {
            "date": "Date", "subject": "Subject", "task": "Task",
            "duration": "Duration", "notes": "Notes",
        }
        widths = {"date": 100, "subject": 130, "task": 180, "duration": 90, "notes": 240}
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], minwidth=60, anchor="w", stretch=(col == "notes"))
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _e: self.open_edit())

        vscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        vscroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=vscroll.set)

        btn_row = ttk.Frame(self)
        btn_row.pack(pady=(0, 10))
        ttk.Button(btn_row, text="Log Session", command=self.open_add).pack(side="left", padx=3)
        ttk.Button(btn_row, text="Edit Session", command=self.open_edit).pack(side="left", padx=3)
        ttk.Button(btn_row, text="Delete Session", command=self.delete_selected).pack(side="left", padx=3)

    def _clear_filters(self) -> None:
        self.period_var.set("All time")
        self.subject_var.set("All")

    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        self._selected_session_id = int(selection[0]) if selection else None

    def refresh(self) -> None:
        subjects = self.dm.get_subjects()
        names = ["All"] + [s.name for s in subjects]
        self._subject_filter_ids = {"All": None}
        for s in subjects:
            self._subject_filter_ids[s.name] = s.id
        self.subject_combo.configure(values=names)
        if self.subject_var.get() not in names:
            self.subject_var.set("All")
        subject_id = self._subject_filter_ids.get(self.subject_var.get())

        for row in self.tree.get_children():
            self.tree.delete(row)

        sessions = self.dm.get_sessions_filtered(period=self.period_var.get(), subject_id=subject_id)
        if not sessions:
            message = ("No study sessions recorded." if not self.dm.sessions
                       else "No study sessions match the current filters.")
            self.tree.insert("", tk.END, values=(message, "", "", "", ""))
        else:
            for session in sessions:
                subject = self.dm.get_subject(session.subject_id)
                task = self.dm.get_task(session.task_id) if session.task_id else None
                if session.task_id and task is None:
                    task_label = "(deleted task)"
                else:
                    task_label = task.title if task else ""
                self.tree.insert(
                    "", tk.END, iid=str(session.id),
                    values=(
                        session.date,
                        subject.name if subject else "(deleted)",
                        task_label,
                        self.dm.format_duration(session.duration_minutes),
                        session.notes,
                    ),
                )

        summary = self.dm.get_study_time_summary()
        fmt = self.dm.format_duration
        self.summary_var.set(
            f"Total: {fmt(summary['total'])}   Today: {fmt(summary['today'])}   "
            f"This week: {fmt(summary['this_week'])}   This month: {fmt(summary['this_month'])}"
        )
        self._selected_session_id = None

    def open_add(self) -> None:
        if not self.dm.get_subjects():
            messagebox.showinfo("Log Session", "Add a subject first (on the Subjects tab).")
            return
        SessionDialog(
            self, dm=self.dm, title="Log Study Session", on_submit=self._add_session,
            default_minutes=self.app.settings.get("default_session_minutes"),
        )

    def _add_session(self, values: dict) -> bool:
        try:
            self.dm.add_session(**values)
        except USER_ERRORS as exc:
            messagebox.showerror("Cannot log session", str(exc))
            return False
        self.app.notify_data_changed()
        return True

    def open_edit(self) -> None:
        if self._selected_session_id is None:
            messagebox.showinfo("Edit Session", "Select a session first.")
            return
        session = self.dm.get_session(self._selected_session_id)
        SessionDialog(
            self, dm=self.dm, title="Edit Study Session",
            on_submit=lambda values: self._edit_session(session.id, values), initial_session=session,
        )

    def _edit_session(self, session_id: int, values: dict) -> bool:
        try:
            self.dm.edit_session(session_id, **values)
        except USER_ERRORS as exc:
            messagebox.showerror("Cannot edit session", str(exc))
            return False
        self.app.notify_data_changed()
        return True

    def delete_selected(self) -> None:
        if self._selected_session_id is None:
            messagebox.showinfo("Delete Session", "Select a session first.")
            return
        session = self.dm.get_session(self._selected_session_id)
        subject = self.dm.get_subject(session.subject_id)
        label = (
            f"{session.date} - {subject.name if subject else '(deleted)'} "
            f"({self.dm.format_duration(session.duration_minutes)})"
        )
        if not messagebox.askyesno("Delete Session", f"Delete this session?\n{label}"):
            return
        try:
            self.dm.delete_session(session.id)
        except USER_ERRORS as exc:
            messagebox.showerror("Cannot delete session", str(exc))
            return
        self.app.notify_data_changed()


class AnalyticsTab(ttk.Frame):
    """Dashboard totals, charts, a subject stats table, and a productivity summary.

    Study-time figures (dashboard + charts) respect the period selector.
    Task completion is a current-state metric and is always shown as of
    right now, independent of the period selector -- a task doesn't stop
    being "completed" because you changed the analytics window.
    """

    def __init__(self, parent: ttk.Notebook, app: StudyPlannerApp):
        super().__init__(parent)
        self.app = app
        self.dm = app.dm
        self._build()

    def _build(self) -> None:
        dash_frame = ttk.LabelFrame(self, text="Dashboard")
        dash_frame.pack(fill="x", padx=8, pady=(10, 6))
        self.task_summary_var = tk.StringVar()
        self.study_summary_var = tk.StringVar()
        self.highlight_var = tk.StringVar()
        self.upcoming_var = tk.StringVar()
        ttk.Label(dash_frame, textvariable=self.task_summary_var).pack(anchor="w", padx=8, pady=(6, 0))
        ttk.Label(dash_frame, textvariable=self.study_summary_var).pack(anchor="w", padx=8)
        ttk.Label(dash_frame, textvariable=self.upcoming_var).pack(anchor="w", padx=8)
        ttk.Label(dash_frame, textvariable=self.highlight_var, foreground="#555555").pack(
            anchor="w", padx=8, pady=(0, 6)
        )

        controls = ttk.Frame(self)
        controls.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Label(controls, text="Analytics period:").pack(side="left")
        self.period_var = tk.StringVar(value="This week")
        ttk.Combobox(
            controls, textvariable=self.period_var, values=PERIOD_OPTIONS,
            state="readonly", width=12,
        ).pack(side="left", padx=(4, 12))
        self.period_var.trace_add("write", lambda *_: self.refresh())
        self.productivity_var = tk.StringVar()
        ttk.Label(controls, textvariable=self.productivity_var, foreground="#555555").pack(side="left")

        # Packed before the charts with side="bottom" so that when the window
        # is short, the charts shrink instead of the table being cut off.
        table_frame = ttk.LabelFrame(self, text="Subject Statistics")
        table_frame.pack(side="bottom", fill="both", expand=True, padx=8, pady=(4, 10))
        self._build_stats_table(table_frame)

        if HAS_MATPLOTLIB:
            self._build_charts()
        else:
            ttk.Label(
                self, foreground="#8a4b00",
                text="Charts are unavailable because matplotlib is not installed "
                     "(pip install -r requirements.txt). All other statistics still work.",
            ).pack(anchor="w", padx=8, pady=8)

    def _build_charts(self) -> None:
        # Three equal-width charts in one row that absorbs any spare height;
        # uniform grid columns keep them the same size as the window resizes.
        charts_row = ttk.Frame(self)
        charts_row.pack(fill="both", expand=True, padx=8, pady=4)
        charts_row.rowconfigure(0, weight=1)

        def make_chart(column: int):
            charts_row.columnconfigure(column, weight=1, uniform="chart")
            fig = Figure(figsize=(3.6, 2.6), dpi=100, layout="tight")
            canvas = FigureCanvasTkAgg(fig, master=charts_row)
            canvas.get_tk_widget().grid(row=0, column=column, sticky="nsew", padx=3)
            return fig, fig.add_subplot(111), canvas

        self.subject_fig, self.subject_ax, self.subject_canvas = make_chart(0)
        self.trend_fig, self.trend_ax, self.trend_canvas = make_chart(1)
        self.completion_fig, self.completion_ax, self.completion_canvas = make_chart(2)

    def _build_stats_table(self, table_frame: ttk.LabelFrame) -> None:
        columns = ("subject", "tasks", "completed", "pending", "overdue", "pct", "time")
        headings = {
            "subject": "Subject", "tasks": "Tasks", "completed": "Completed", "pending": "Pending",
            "overdue": "Overdue", "pct": "Progress", "time": "Study Time (period)",
        }
        self.stats_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=5)
        for col in columns:
            width = 150 if col == "subject" else (130 if col == "time" else 90)
            self.stats_tree.heading(col, text=headings[col])
            self.stats_tree.column(col, width=width, minwidth=50, anchor="w")
        self.stats_tree.pack(fill="both", expand=True, padx=6, pady=6)

    def refresh(self) -> None:
        dash = self.dm.get_dashboard_summary()
        task = dash["task"]
        study = dash["study"]
        fmt = self.dm.format_duration

        self.task_summary_var.set(
            f"Tasks -- Total: {task['total']}   Completed: {task['completed']}   "
            f"Pending: {task['not_started'] + task['in_progress']}   Overdue: {task['overdue']}"
        )
        self.study_summary_var.set(
            f"Study Time -- Total: {fmt(study['total'])}   Today: {fmt(study['today'])}   "
            f"This week: {fmt(study['this_week'])}   This month: {fmt(study['this_month'])}"
        )
        highlight_parts = []
        if dash["most_studied_subject"]:
            highlight_parts.append(f"Most studied overall: {dash['most_studied_subject']}")
        if dash["best_completion_subject"]:
            highlight_parts.append(
                f"Highest completion: {dash['best_completion_subject']} ({dash['best_completion_pct']:.1f}%)"
            )
        self.highlight_var.set("   |   ".join(highlight_parts) if highlight_parts else "Not enough data yet.")

        window = self.app.settings.get("reminder_window_days")
        groups = reminders.get_reminders(self.dm, window)
        upcoming = groups["today"] + groups["tomorrow"] + groups["soon"]
        if upcoming:
            nxt = upcoming[0]
            self.upcoming_var.set(
                f"Upcoming -- {len(upcoming)} due in the next {window} day(s); "
                f"next: {nxt.title} ({nxt.deadline})"
            )
        else:
            self.upcoming_var.set(f"Upcoming -- No deadlines in the next {window} day(s).")

        period = self.period_var.get()
        if HAS_MATPLOTLIB:
            for render in (self._render_subject_chart, self._render_trend_chart, self._render_completion_chart):
                self._safe_render(render, period)
        self._render_stats_table(period)

        prod = self.dm.get_productivity_summary(period=period)
        productivity_text = (
            f"This period -- Sessions: {prod['session_count']}   "
            f"Avg length: {fmt(prod['avg_session_minutes'])}   "
            f"Tasks completed: {prod['tasks_completed']}"
        )
        if prod["most_studied_subject"]:
            productivity_text += f"   Most studied: {prod['most_studied_subject']}"
        self.productivity_var.set(productivity_text)

    @staticmethod
    def _safe_render(render, period: str) -> None:
        # A chart that fails to draw (e.g. a matplotlib backend problem)
        # shouldn't take the statistics text/table down with it.
        try:
            render(period)
        except (ValueError, RuntimeError, OSError) as exc:
            print(f"Chart rendering failed: {exc}")

    def _render_subject_chart(self, period: str) -> None:
        ax = self.subject_ax
        ax.clear()
        ax.set_title("Study Time by Subject", fontsize=9)
        data = self.dm.get_study_minutes_by_subject(period=period)
        if not data:
            ax.text(0.5, 0.5, "Not enough data\nfor this period.", ha="center", va="center", fontsize=8)
            ax.set_xticks([])
            ax.set_yticks([])
        else:
            names = [name for name, _ in data]
            hours = [minutes / 60 for _, minutes in data]
            ax.bar(names, hours, color="#4a7ebb")
            ax.set_ylabel("Hours", fontsize=8)
            ax.tick_params(axis="x", labelrotation=30, labelsize=7)
            ax.tick_params(axis="y", labelsize=7)
        self.subject_canvas.draw()

    def _render_trend_chart(self, period: str) -> None:
        ax = self.trend_ax
        ax.clear()
        ax.set_title("Daily Study Time", fontsize=9)
        # A single-day line isn't informative -- show the week's shape instead.
        chart_period = "This week" if period == "Today" else period
        data = self.dm.get_study_minutes_by_day(period=chart_period)
        if not data:
            ax.text(0.5, 0.5, "Not enough data\nfor this period.", ha="center", va="center", fontsize=8)
            ax.set_xticks([])
            ax.set_yticks([])
        else:
            labels = [day[5:] for day, _ in data]  # MM-DD
            hours = [minutes / 60 for _, minutes in data]
            ax.plot(labels, hours, marker="o", color="#4a7ebb", markersize=3, linewidth=1.5)
            ax.set_ylim(bottom=0)
            ax.set_ylabel("Hours", fontsize=8)
            stride = max(1, len(labels) // 8)
            tick_positions = list(range(0, len(labels), stride))
            ax.set_xticks(tick_positions)
            ax.set_xticklabels([labels[i] for i in tick_positions])
            ax.tick_params(axis="x", labelrotation=45, labelsize=7)
            ax.tick_params(axis="y", labelsize=7)
        self.trend_canvas.draw()

    def _render_completion_chart(self, _period: str = "") -> None:
        ax = self.completion_ax
        ax.clear()
        ax.set_title("Task Completion by Subject", fontsize=9)
        data = self.dm.get_task_completion_by_subject()
        if not data:
            ax.text(0.5, 0.5, "No subjects have been added yet.", ha="center", va="center", fontsize=8)
            ax.set_xticks([])
            ax.set_yticks([])
        else:
            names = [name for name, _, _ in data]
            pct = [(completed / total * 100) if total else 0 for _, completed, total in data]
            bars = ax.barh(names, pct, color="#5aa469")
            ax.set_xlim(0, 100)
            ax.set_xlabel("% complete", fontsize=8)
            ax.tick_params(axis="both", labelsize=7)
            for bar, (_, completed, total) in zip(bars, data):
                ax.text(
                    min(bar.get_width() + 2, 96), bar.get_y() + bar.get_height() / 2,
                    f"{completed}/{total}", va="center", fontsize=7,
                )
        self.completion_canvas.draw()

    def _render_stats_table(self, period: str) -> None:
        for row in self.stats_tree.get_children():
            self.stats_tree.delete(row)
        subjects = self.dm.get_subjects()
        if not subjects:
            self.stats_tree.insert("", tk.END, values=("No subjects have been added yet.", "", "", "", "", "", ""))
            return
        for subject in subjects:
            stats = self.dm.get_subject_summary(subject.id, period=period)
            self.stats_tree.insert(
                "", tk.END, iid=f"stat{subject.id}",
                values=(
                    subject.name, stats["total"], stats["completed"], stats["pending"],
                    stats["overdue"], f"{stats['completion_pct']:.1f}%",
                    self.dm.format_duration(stats["study_minutes"]),
                ),
            )


def _setup_dialog(dialog: tk.Toplevel, parent: tk.Widget, title: str, submit: Callable[[], None]) -> None:
    """Shared modal behavior so every dialog acts the same way."""
    dialog.title(title)
    dialog.resizable(False, False)
    dialog.transient(parent.winfo_toplevel())
    dialog.bind("<Escape>", lambda _e: dialog.destroy())
    # Return submits, except inside multi-line Text fields where it's a newline.
    dialog.bind("<Return>", lambda e: None if isinstance(e.widget, tk.Text) else submit())
    dialog.after_idle(lambda: _center_on_parent(dialog, parent.winfo_toplevel()))
    dialog.grab_set()


def _center_on_parent(dialog: tk.Toplevel, parent: tk.Misc) -> None:
    dialog.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - dialog.winfo_width()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - dialog.winfo_height()) // 3
    dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")


class SubjectDialog(tk.Toplevel):
    """Modal form for adding or editing a subject."""

    def __init__(
        self,
        parent: tk.Widget,
        title: str,
        on_submit: Callable[[str, str], bool],
        initial_name: str = "",
        initial_description: str = "",
    ):
        super().__init__(parent)
        _setup_dialog(self, parent, title, self._submit)

        self.on_submit = on_submit

        ttk.Label(self, text="Name:").grid(row=0, column=0, sticky="w", padx=8, pady=(10, 2))
        self.name_var = tk.StringVar(value=initial_name)
        entry = ttk.Entry(self, textvariable=self.name_var, width=32)
        entry.grid(row=0, column=1, padx=8, pady=(10, 2))
        entry.focus_set()

        ttk.Label(self, text="Description:").grid(row=1, column=0, sticky="nw", padx=8, pady=2)
        self.description_text = tk.Text(self, width=32, height=4)
        self.description_text.insert("1.0", initial_description)
        self.description_text.grid(row=1, column=1, padx=8, pady=2)

        btn_row = ttk.Frame(self)
        btn_row.grid(row=2, column=0, columnspan=2, pady=10)
        ttk.Button(btn_row, text="Save", command=self._submit).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="left", padx=4)

    def _submit(self) -> None:
        name = self.name_var.get()
        description = self.description_text.get("1.0", "end").strip()
        # Keep the dialog open on a validation error so input isn't lost.
        if self.on_submit(name, description):
            self.destroy()


class TaskDialog(tk.Toplevel):
    """Modal form for adding or editing a task."""

    def __init__(
        self,
        parent: tk.Widget,
        title: str,
        subjects: list[Subject],
        on_submit: Callable[[dict], bool],
        initial_task: Optional[Task] = None,
        study_minutes: Optional[int] = None,
        format_duration: Optional[Callable[[int], str]] = None,
        default_priority: str = "Medium",
        default_status: str = "Not Started",
    ):
        super().__init__(parent)
        _setup_dialog(self, parent, title, self._submit)

        self.on_submit = on_submit
        self._subject_by_name = {s.name: s.id for s in subjects}
        subject_names = [s.name for s in subjects]

        row = 0
        ttk.Label(self, text="Title:").grid(row=row, column=0, sticky="w", padx=8, pady=(10, 2))
        self.title_var = tk.StringVar(value=initial_task.title if initial_task else "")
        entry = ttk.Entry(self, textvariable=self.title_var, width=34)
        entry.grid(row=row, column=1, padx=8, pady=(10, 2))
        entry.focus_set()
        row += 1

        ttk.Label(self, text="Subject:").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        self.subject_var = tk.StringVar()
        ttk.Combobox(
            self, textvariable=self.subject_var, values=subject_names, state="readonly", width=31
        ).grid(row=row, column=1, padx=8, pady=2)
        if initial_task:
            current = next((s.name for s in subjects if s.id == initial_task.subject_id), "")
            self.subject_var.set(current)
        elif subject_names:
            self.subject_var.set(subject_names[0])
        row += 1

        ttk.Label(self, text="Priority:").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        self.priority_var = tk.StringVar(value=initial_task.priority if initial_task else default_priority)
        ttk.Combobox(
            self, textvariable=self.priority_var, values=list(PRIORITIES), state="readonly", width=31
        ).grid(row=row, column=1, padx=8, pady=2)
        row += 1

        ttk.Label(self, text="Deadline (YYYY-MM-DD):").grid(
            row=row, column=0, sticky="w", padx=8, pady=2
        )
        self.deadline_var = tk.StringVar(value=initial_task.deadline if initial_task else "")
        ttk.Entry(self, textvariable=self.deadline_var, width=34).grid(row=row, column=1, padx=8, pady=2)
        row += 1

        ttk.Label(self, text="Status:").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        self.status_var = tk.StringVar(value=initial_task.status if initial_task else default_status)
        ttk.Combobox(
            self, textvariable=self.status_var, values=list(STATUSES), state="readonly", width=31
        ).grid(row=row, column=1, padx=8, pady=2)
        row += 1

        if initial_task:
            info = f"Created: {initial_task.created_at}"
            if initial_task.completion_date:
                info += f"    |    Completed: {initial_task.completion_date}"
            ttk.Label(self, text=info, foreground="#555555").grid(
                row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 2)
            )
            row += 1
            if study_minutes and format_duration:
                # Study time spent is informational only -- it never implies
                # completion; status is still set explicitly above.
                ttk.Label(
                    self, text=f"Study time logged: {format_duration(study_minutes)}",
                    foreground="#555555",
                ).grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 2))
                row += 1

        ttk.Label(self, text="Description:").grid(row=row, column=0, sticky="nw", padx=8, pady=2)
        self.description_text = tk.Text(self, width=34, height=4)
        if initial_task:
            self.description_text.insert("1.0", initial_task.description)
        self.description_text.grid(row=row, column=1, padx=8, pady=2)
        row += 1

        btn_row = ttk.Frame(self)
        btn_row.grid(row=row, column=0, columnspan=2, pady=10)
        ttk.Button(btn_row, text="Save", command=self._submit).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="left", padx=4)

    def _submit(self) -> None:
        subject_name = self.subject_var.get()
        subject_id = self._subject_by_name.get(subject_name)
        if subject_id is None:
            messagebox.showerror("Cannot save task", "Please select a valid subject.")
            return
        values = {
            "title": self.title_var.get(),
            "subject_id": subject_id,
            "priority": self.priority_var.get(),
            "deadline": self.deadline_var.get(),
            "status": self.status_var.get(),
            "description": self.description_text.get("1.0", "end").strip(),
        }
        if self.on_submit(values):
            self.destroy()


class SessionDialog(tk.Toplevel):
    """Modal form for logging or editing a study session."""

    NO_TASK_LABEL = "(none)"

    def __init__(
        self,
        parent: tk.Widget,
        dm: DataManager,
        title: str,
        on_submit: Callable[[dict], bool],
        initial_session: Optional[StudySession] = None,
        default_minutes: Optional[int] = None,
    ):
        super().__init__(parent)
        _setup_dialog(self, parent, title, self._submit)

        self.dm = dm
        self.on_submit = on_submit
        self._task_by_label: dict[str, Optional[int]] = {}
        subjects = dm.get_subjects()
        self._subject_by_name = {s.name: s.id for s in subjects}
        subject_names = [s.name for s in subjects]

        row = 0
        ttk.Label(self, text="Subject:").grid(row=row, column=0, sticky="w", padx=8, pady=(10, 2))
        self.subject_var = tk.StringVar()
        self.subject_combo = ttk.Combobox(
            self, textvariable=self.subject_var, values=subject_names, state="readonly", width=31,
        )
        self.subject_combo.grid(row=row, column=1, padx=8, pady=(10, 2))
        self.subject_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_task_options())
        row += 1

        ttk.Label(self, text="Task (optional):").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        self.task_var = tk.StringVar()
        self.task_combo = ttk.Combobox(self, textvariable=self.task_var, state="readonly", width=31)
        self.task_combo.grid(row=row, column=1, padx=8, pady=2)
        row += 1

        ttk.Label(self, text="Date (YYYY-MM-DD):").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        default_date = initial_session.date if initial_session else date.today().strftime(DATE_FORMAT)
        self.date_var = tk.StringVar(value=default_date)
        ttk.Entry(self, textvariable=self.date_var, width=34).grid(row=row, column=1, padx=8, pady=2)
        row += 1

        ttk.Label(self, text="Duration (minutes):").grid(row=row, column=0, sticky="w", padx=8, pady=2)
        self.duration_var = tk.StringVar(
            value=str(initial_session.duration_minutes) if initial_session else str(default_minutes or "")
        )
        entry = ttk.Entry(self, textvariable=self.duration_var, width=34)
        entry.grid(row=row, column=1, padx=8, pady=2)
        entry.focus_set()
        row += 1

        ttk.Label(self, text="Notes:").grid(row=row, column=0, sticky="nw", padx=8, pady=2)
        self.notes_text = tk.Text(self, width=34, height=4)
        if initial_session:
            self.notes_text.insert("1.0", initial_session.notes)
        self.notes_text.grid(row=row, column=1, padx=8, pady=2)
        row += 1

        btn_row = ttk.Frame(self)
        btn_row.grid(row=row, column=0, columnspan=2, pady=10)
        ttk.Button(btn_row, text="Save", command=self._submit).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="left", padx=4)

        # Set subject/task selection last, once every widget exists.
        if initial_session:
            subject = dm.get_subject(initial_session.subject_id)
            if subject:
                self.subject_var.set(subject.name)
        elif subject_names:
            self.subject_var.set(subject_names[0])
        self._refresh_task_options(
            initial_task_id=initial_session.task_id if initial_session else None
        )

    def _refresh_task_options(self, initial_task_id: Optional[int] = None) -> None:
        subject_id = self._subject_by_name.get(self.subject_var.get())
        tasks = (
            [t for t in self.dm.get_tasks() if t.subject_id == subject_id] if subject_id else []
        )
        self._task_by_label = {self.NO_TASK_LABEL: None}
        labels = [self.NO_TASK_LABEL]
        selected_label = self.NO_TASK_LABEL
        for task in tasks:
            self._task_by_label[task.title] = task.id
            labels.append(task.title)
            if initial_task_id is not None and task.id == initial_task_id:
                selected_label = task.title
        self.task_combo.configure(values=labels)
        self.task_var.set(selected_label)

    def _submit(self) -> None:
        subject_id = self._subject_by_name.get(self.subject_var.get())
        if subject_id is None:
            messagebox.showerror("Cannot save session", "Please select a valid subject.")
            return
        task_id = self._task_by_label.get(self.task_var.get())
        values = {
            "subject_id": subject_id,
            "task_id": task_id,
            "date_str": self.date_var.get(),
            "duration_minutes": self.duration_var.get(),
            "notes": self.notes_text.get("1.0", "end").strip(),
        }
        if self.on_submit(values):
            self.destroy()
