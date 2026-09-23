"""
Tkinter desktop interface for the Study Planner application (Phase 2).

This module only handles presentation and user interaction. All data
rules (validation, persistence, ID assignment, filtering/sorting/deadline
classification) live in data_manager.py -- no tab computes those itself,
they all call into DataManager so the logic is defined exactly once.

Layout: a ttk.Notebook with four tabs -- Tasks (search/filter/sort table),
Upcoming (overdue + due-this-week), Calendar (month view + day detail),
and Subjects (CRUD + per-subject task counts).
"""

from __future__ import annotations

import calendar
import tkinter as tk
from datetime import date
from tkinter import ttk, messagebox
from typing import Callable, Optional

from data_manager import DataManager, SubjectHasTasksError, ValidationError
from models import PRIORITIES, STATUSES, Subject, Task

DEADLINE_FILTERS = ("All", "Today", "This week", "Overdue", "Upcoming")
STATUS_FILTER_OPTIONS = ("All",) + STATUSES
PRIORITY_FILTER_OPTIONS = ("All",) + PRIORITIES

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
    """Top-level application: owns the main window and the four tabs."""

    def __init__(self, root: tk.Tk, data_manager: DataManager):
        self.root = root
        self.dm = data_manager

        self.root.title("Study Planner")
        self.root.geometry("980x600")
        self.root.minsize(820, 480)

        header = ttk.Label(self.root, text="Study Planner", font=("Segoe UI", 16, "bold"))
        header.pack(pady=(10, 4))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.tasks_tab = TasksTab(self.notebook, self)
        self.upcoming_tab = UpcomingTab(self.notebook, self)
        self.calendar_tab = CalendarTab(self.notebook, self)
        self.subjects_tab = SubjectsTab(self.notebook, self)

        self.notebook.add(self.tasks_tab, text="Tasks")
        self.notebook.add(self.upcoming_tab, text="Upcoming")
        self.notebook.add(self.calendar_tab, text="Calendar")
        self.notebook.add(self.subjects_tab, text="Subjects")

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self.refresh_all()

        if self.dm.startup_warning:
            messagebox.showwarning("Study Planner", self.dm.startup_warning)

    def _on_tab_changed(self, _event=None) -> None:
        # Refresh whichever tab just became visible, in case data changed
        # on another tab (e.g. a task was added on the Tasks tab).
        tab = self.notebook.nametowidget(self.notebook.select())
        if hasattr(tab, "refresh"):
            tab.refresh()

    def refresh_all(self) -> None:
        self.tasks_tab.refresh()
        self.upcoming_tab.refresh()
        self.calendar_tab.refresh()
        self.subjects_tab.refresh()

    def notify_data_changed(self) -> None:
        """Called by any tab after it adds/edits/deletes data, so every
        other tab (which may be showing stale counts) stays in sync."""
        self.refresh_all()


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
        filter_bar = ttk.Frame(self)
        filter_bar.pack(fill="x", padx=6, pady=(8, 2))

        ttk.Label(filter_bar, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        ttk.Entry(filter_bar, textvariable=self.search_var, width=20).pack(
            side="left", padx=(4, 12)
        )
        self.search_var.trace_add("write", lambda *_: self.refresh())

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

        ttk.Button(filter_bar, text="Clear Filters", command=self._clear_filters).pack(side="left")

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
            self.tree.column(col, width=widths[col], anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Button-3>", self._on_right_click)

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
        ttk.Label(btn_row, text="  (right-click a task to change its status quickly)",
                  foreground="#777777").pack(side="left")

        self.context_menu = tk.Menu(self, tearoff=0)
        for status in STATUSES:
            self.context_menu.add_command(
                label=f"Mark {status}", command=lambda s=status: self._quick_status(s)
            )

    # -- filter bar behavior ---------------------------------------------

    def _clear_filters(self) -> None:
        self.search_var.set("")
        self.status_var.set("All")
        self.priority_var.set("All")
        self.subject_var.set("All")
        self.deadline_var.set("All")
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
        except ValidationError as exc:
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
                "No tasks match the current filters." if total else "No tasks yet -- add one to get started."
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
            messagebox.showinfo("Add Task", "Add a subject first.")
            return
        TaskDialog(self, title="Add Task", subjects=self.dm.get_subjects(), on_submit=self._add_task)

    def _add_task(self, values: dict) -> None:
        try:
            self.dm.add_task(**values)
        except ValidationError as exc:
            messagebox.showerror("Cannot add task", str(exc))
            return
        self.app.notify_data_changed()

    def open_edit(self) -> None:
        if self._selected_task_id is None:
            messagebox.showinfo("Edit Task", "Select a task first.")
            return
        task = self.dm.get_task(self._selected_task_id)
        TaskDialog(
            self, title="Edit Task", subjects=self.dm.get_subjects(),
            on_submit=lambda values: self._edit_task(task.id, values), initial_task=task,
        )

    def _edit_task(self, task_id: int, values: dict) -> None:
        try:
            self.dm.edit_task(task_id, **values)
        except ValidationError as exc:
            messagebox.showerror("Cannot edit task", str(exc))
            return
        self.app.notify_data_changed()

    def delete_selected(self) -> None:
        if self._selected_task_id is None:
            messagebox.showinfo("Delete Task", "Select a task first.")
            return
        task = self.dm.get_task(self._selected_task_id)
        if not messagebox.askyesno("Delete Task", f"Delete task '{task.title}'?"):
            return
        try:
            self.dm.delete_task(task.id)
        except ValidationError as exc:
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
            tree.column(col, width=220 if col == "title" else 110, anchor="w")
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
            self.overdue_tree.insert("", tk.END, values=("No overdue tasks", "", "", "", ""))
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
                "", tk.END, values=("Nothing due in the next 7 days", "", "", "", "")
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
        self._selected_date: str = today.strftime("%Y-%m-%d")
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

        self.grid_frame = ttk.Frame(self)
        self.grid_frame.pack(padx=8, pady=4)

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
                self.grid_frame, text=name, width=6, anchor="center", font=("Segoe UI", 9, "bold")
            ).grid(row=0, column=col, padx=1, pady=1)

        days_with_tasks = self.dm.get_task_days_in_month(self._year, self._month)
        today_str = date.today().strftime("%Y-%m-%d")

        cal = calendar.Calendar(firstweekday=0)  # weeks start Monday
        for r, week in enumerate(cal.monthdayscalendar(self._year, self._month), start=1):
            for c, day in enumerate(week):
                if day == 0:
                    ttk.Label(self.grid_frame, text="", width=6).grid(row=r, column=c, padx=1, pady=1)
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
                    self.grid_frame, text=label, width=6,
                    command=lambda d=date_str: self._select_date(d),
                ).grid(row=r, column=c, padx=1, pady=1)

    def _render_day_tasks(self) -> None:
        for row in self.day_tree.get_children():
            self.day_tree.delete(row)
        self.selected_date_var.set(f"Tasks on {self._selected_date}")
        tasks = self.dm.get_tasks_by_date(self._selected_date)
        if not tasks:
            self.day_tree.insert("", tk.END, values=("No tasks due on this date", "", "", ""))
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
        ttk.Button(btn_row, text="Add", command=self.open_add).pack(side="left", padx=3)
        ttk.Button(btn_row, text="Edit", command=self.open_edit).pack(side="left", padx=3)
        ttk.Button(btn_row, text="Delete", command=self.delete_selected).pack(side="left", padx=3)

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
            self.detail_var.set("Select a subject to see its details.")
            return
        stats = self.dm.get_subject_summary(subject.id)
        description = subject.description or "(no description)"
        self.detail_var.set(
            f"{subject.name}\n\n{description}\n\n"
            f"Total tasks: {stats['total']}\n"
            f"Completed: {stats['completed']}\n"
            f"Pending: {stats['pending']}"
        )

    def open_add(self) -> None:
        SubjectDialog(self, title="Add Subject", on_submit=self._add_subject)

    def _add_subject(self, name: str, description: str) -> None:
        try:
            self.dm.add_subject(name, description)
        except ValidationError as exc:
            messagebox.showerror("Cannot add subject", str(exc))
            return
        self.app.notify_data_changed()

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

    def _edit_subject(self, subject_id: int, name: str, description: str) -> None:
        try:
            self.dm.edit_subject(subject_id, name, description)
        except ValidationError as exc:
            messagebox.showerror("Cannot edit subject", str(exc))
            return
        self.app.notify_data_changed()

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
            messagebox.showwarning(
                "Cannot delete subject",
                f"'{exc.subject.name}' still has {exc.task_count} task(s) attached.\n"
                "Delete or reassign those tasks first, then try again.",
            )
            return
        except ValidationError as exc:
            messagebox.showerror("Cannot delete subject", str(exc))
            return
        self.app.notify_data_changed()


class SubjectDialog(tk.Toplevel):
    """Modal form for adding or editing a subject."""

    def __init__(
        self,
        parent: tk.Widget,
        title: str,
        on_submit: Callable[[str, str], None],
        initial_name: str = "",
        initial_description: str = "",
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

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
        self.on_submit(name, description)
        self.destroy()


class TaskDialog(tk.Toplevel):
    """Modal form for adding or editing a task."""

    def __init__(
        self,
        parent: tk.Widget,
        title: str,
        subjects: list[Subject],
        on_submit: Callable[[dict], None],
        initial_task: Optional[Task] = None,
    ):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

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
        self.priority_var = tk.StringVar(value=initial_task.priority if initial_task else "Medium")
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
        self.status_var = tk.StringVar(value=initial_task.status if initial_task else "Not Started")
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
        self.on_submit(values)
        self.destroy()
