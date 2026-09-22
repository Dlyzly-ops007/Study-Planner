"""
Tkinter desktop interface for the Study Planner application.

This module only handles presentation and user interaction. All data
rules (validation, persistence, ID assignment) live in data_manager.py,
and StudyPlannerApp never edits self.dm.subjects / self.dm.tasks directly
-- it always goes through DataManager methods.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Callable

from data_manager import DataManager, ValidationError, SubjectHasTasksError
from models import PRIORITIES, STATUSES, Subject, Task


class StudyPlannerApp:
    """Top-level application: owns the main window and wires up all panels."""

    def __init__(self, root: tk.Tk, data_manager: DataManager):
        self.root = root
        self.dm = data_manager

        self.root.title("Study Planner")
        self.root.geometry("900x520")
        self.root.minsize(760, 420)

        self._subject_order: list[Subject] = []
        self._selected_subject_id: Optional[int] = None
        self._selected_task_id: Optional[int] = None

        self._build_layout()
        self.refresh_subjects()
        self.refresh_tasks()

        if self.dm.startup_warning:
            messagebox.showwarning("Study Planner", self.dm.startup_warning)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        header = ttk.Label(self.root, text="Study Planner", font=("Segoe UI", 16, "bold"))
        header.pack(pady=(10, 4))

        self.summary_var = tk.StringVar()
        ttk.Label(self.root, textvariable=self.summary_var, font=("Segoe UI", 10)).pack(
            pady=(0, 8)
        )

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=3)
        body.rowconfigure(0, weight=1)

        self._build_subject_panel(body)
        self._build_task_panel(body)

    def _build_subject_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Subjects")
        frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.subject_listbox = tk.Listbox(frame, exportselection=False)
        self.subject_listbox.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        self.subject_listbox.bind("<<ListboxSelect>>", self._on_subject_select)

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.subject_listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        self.subject_listbox.configure(yscrollcommand=scrollbar.set)

        btn_row = ttk.Frame(frame)
        btn_row.grid(row=1, column=0, columnspan=2, pady=(0, 6))
        ttk.Button(btn_row, text="Add", command=self.open_add_subject).pack(side="left", padx=3)
        ttk.Button(btn_row, text="Edit", command=self.open_edit_subject).pack(side="left", padx=3)
        ttk.Button(btn_row, text="Delete", command=self.delete_subject).pack(side="left", padx=3)

    def _build_task_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Tasks")
        frame.grid(row=0, column=1, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        columns = ("title", "subject", "priority", "deadline", "status")
        headings = {
            "title": "Task",
            "subject": "Subject",
            "priority": "Priority",
            "deadline": "Deadline",
            "status": "Status",
        }
        widths = {"title": 220, "subject": 120, "priority": 80, "deadline": 90, "status": 110}

        self.task_tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        for col in columns:
            self.task_tree.heading(col, text=headings[col])
            self.task_tree.column(col, width=widths[col], anchor="w")
        self.task_tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        self.task_tree.bind("<<TreeviewSelect>>", self._on_task_select)

        vscroll = ttk.Scrollbar(frame, orient="vertical", command=self.task_tree.yview)
        vscroll.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        self.task_tree.configure(yscrollcommand=vscroll.set)

        btn_row = ttk.Frame(frame)
        btn_row.grid(row=1, column=0, columnspan=2, pady=(0, 6))
        ttk.Button(btn_row, text="Add", command=self.open_add_task).pack(side="left", padx=3)
        ttk.Button(btn_row, text="Edit", command=self.open_edit_task).pack(side="left", padx=3)
        ttk.Button(btn_row, text="Delete", command=self.delete_task).pack(side="left", padx=3)

    # ------------------------------------------------------------------
    # Refresh helpers
    # ------------------------------------------------------------------

    def refresh_subjects(self) -> None:
        self.subject_listbox.delete(0, tk.END)
        self._subject_order = self.dm.get_subjects()
        for subject in self._subject_order:
            self.subject_listbox.insert(tk.END, subject.name)
        self._selected_subject_id = None

    def refresh_tasks(self) -> None:
        for row in self.task_tree.get_children():
            self.task_tree.delete(row)
        for task in self.dm.get_tasks():
            subject = self.dm.get_subject(task.subject_id)
            subject_name = subject.name if subject else "(deleted)"
            self.task_tree.insert(
                "",
                tk.END,
                iid=str(task.id),
                values=(task.title, subject_name, task.priority, task.deadline, task.status),
            )
        self._selected_task_id = None
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        summary = self.dm.get_summary()
        self.summary_var.set(
            f"Total tasks: {summary['total']}   |   "
            f"Completed: {summary['completed']}   |   "
            f"Pending: {summary['pending']}"
        )

    # ------------------------------------------------------------------
    # Selection handling
    # ------------------------------------------------------------------

    def _on_subject_select(self, _event=None) -> None:
        selection = self.subject_listbox.curselection()
        self._selected_subject_id = (
            self._subject_order[selection[0]].id if selection else None
        )

    def _on_task_select(self, _event=None) -> None:
        selection = self.task_tree.selection()
        self._selected_task_id = int(selection[0]) if selection else None

    # ------------------------------------------------------------------
    # Subject actions
    # ------------------------------------------------------------------

    def open_add_subject(self) -> None:
        SubjectDialog(self.root, title="Add Subject", on_submit=self._add_subject)

    def _add_subject(self, name: str, description: str) -> None:
        try:
            self.dm.add_subject(name, description)
        except ValidationError as exc:
            messagebox.showerror("Cannot add subject", str(exc))
            return
        self.refresh_subjects()

    def open_edit_subject(self) -> None:
        if self._selected_subject_id is None:
            messagebox.showinfo("Edit Subject", "Select a subject first.")
            return
        subject = self.dm.get_subject(self._selected_subject_id)
        SubjectDialog(
            self.root,
            title="Edit Subject",
            on_submit=lambda name, desc: self._edit_subject(subject.id, name, desc),
            initial_name=subject.name,
            initial_description=subject.description,
        )

    def _edit_subject(self, subject_id: int, name: str, description: str) -> None:
        try:
            self.dm.edit_subject(subject_id, name, description)
        except ValidationError as exc:
            messagebox.showerror("Cannot edit subject", str(exc))
            return
        self.refresh_subjects()
        self.refresh_tasks()  # subject name is shown inside the task table too

    def delete_subject(self) -> None:
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
        self.refresh_subjects()

    # ------------------------------------------------------------------
    # Task actions
    # ------------------------------------------------------------------

    def open_add_task(self) -> None:
        if not self.dm.get_subjects():
            messagebox.showinfo("Add Task", "Add a subject first.")
            return
        TaskDialog(
            self.root, title="Add Task", subjects=self.dm.get_subjects(), on_submit=self._add_task
        )

    def _add_task(self, values: dict) -> None:
        try:
            self.dm.add_task(**values)
        except ValidationError as exc:
            messagebox.showerror("Cannot add task", str(exc))
            return
        self.refresh_tasks()

    def open_edit_task(self) -> None:
        if self._selected_task_id is None:
            messagebox.showinfo("Edit Task", "Select a task first.")
            return
        task = self.dm.get_task(self._selected_task_id)
        TaskDialog(
            self.root,
            title="Edit Task",
            subjects=self.dm.get_subjects(),
            on_submit=lambda values: self._edit_task(task.id, values),
            initial_task=task,
        )

    def _edit_task(self, task_id: int, values: dict) -> None:
        try:
            self.dm.edit_task(task_id, **values)
        except ValidationError as exc:
            messagebox.showerror("Cannot edit task", str(exc))
            return
        self.refresh_tasks()

    def delete_task(self) -> None:
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
        self.refresh_tasks()


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

        ttk.Label(self, text="Title:").grid(row=0, column=0, sticky="w", padx=8, pady=(10, 2))
        self.title_var = tk.StringVar(value=initial_task.title if initial_task else "")
        entry = ttk.Entry(self, textvariable=self.title_var, width=34)
        entry.grid(row=0, column=1, padx=8, pady=(10, 2))
        entry.focus_set()

        ttk.Label(self, text="Subject:").grid(row=1, column=0, sticky="w", padx=8, pady=2)
        self.subject_var = tk.StringVar()
        ttk.Combobox(
            self, textvariable=self.subject_var, values=subject_names, state="readonly", width=31
        ).grid(row=1, column=1, padx=8, pady=2)
        if initial_task:
            current = next((s.name for s in subjects if s.id == initial_task.subject_id), "")
            self.subject_var.set(current)
        elif subject_names:
            self.subject_var.set(subject_names[0])

        ttk.Label(self, text="Priority:").grid(row=2, column=0, sticky="w", padx=8, pady=2)
        self.priority_var = tk.StringVar(value=initial_task.priority if initial_task else "Medium")
        ttk.Combobox(
            self, textvariable=self.priority_var, values=list(PRIORITIES), state="readonly", width=31
        ).grid(row=2, column=1, padx=8, pady=2)

        ttk.Label(self, text="Deadline (YYYY-MM-DD):").grid(
            row=3, column=0, sticky="w", padx=8, pady=2
        )
        self.deadline_var = tk.StringVar(value=initial_task.deadline if initial_task else "")
        ttk.Entry(self, textvariable=self.deadline_var, width=34).grid(row=3, column=1, padx=8, pady=2)

        ttk.Label(self, text="Status:").grid(row=4, column=0, sticky="w", padx=8, pady=2)
        self.status_var = tk.StringVar(value=initial_task.status if initial_task else "Not Started")
        ttk.Combobox(
            self, textvariable=self.status_var, values=list(STATUSES), state="readonly", width=31
        ).grid(row=4, column=1, padx=8, pady=2)

        ttk.Label(self, text="Description:").grid(row=5, column=0, sticky="nw", padx=8, pady=2)
        self.description_text = tk.Text(self, width=34, height=4)
        if initial_task:
            self.description_text.insert("1.0", initial_task.description)
        self.description_text.grid(row=5, column=1, padx=8, pady=2)

        btn_row = ttk.Frame(self)
        btn_row.grid(row=6, column=0, columnspan=2, pady=10)
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
