"""
Persistence and CRUD logic for the Study Planner application.

DataManager owns the in-memory state (subjects + tasks) and is the single
place that talks to the JSON file on disk. The GUI never touches the file
or the subject/task dicts directly -- it always goes through DataManager
methods, so validation and ID management stay consistent no matter which
part of the app is making a change.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, date, timedelta
from typing import Optional

from models import Subject, Task, PRIORITIES, STATUSES, DATE_FORMAT


class ValidationError(Exception):
    """Raised when user-supplied data fails validation. Message is user-facing."""


class SubjectHasTasksError(Exception):
    """
    Raised when trying to delete a subject that still has tasks attached.

    Phase 1 deletion policy: a subject with existing tasks is never deleted
    automatically and a task is never silently destroyed. The caller (the
    GUI) tells the user how many tasks are attached and asks them to
    delete or reassign those tasks first. This is the simplest rule that
    guarantees no task data is ever lost as a side effect of a subject
    deletion.
    """

    def __init__(self, subject: Subject, task_count: int):
        self.subject = subject
        self.task_count = task_count
        super().__init__(
            f"Subject '{subject.name}' still has {task_count} task(s) attached."
        )


class DataManager:
    """Loads, validates, mutates, and saves the planner's data file."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.subjects: dict[int, Subject] = {}
        self.tasks: dict[int, Task] = {}
        self._next_subject_id = 1
        self._next_task_id = 1
        # Set by load() if the existing data file was corrupted and had to
        # be reset. main.py surfaces this to the user via a message box.
        self.startup_warning: Optional[str] = None
        self.load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load data from disk, creating an empty file if none exists yet."""
        directory = os.path.dirname(self.filepath)
        if directory:
            os.makedirs(directory, exist_ok=True)

        if not os.path.exists(self.filepath):
            self._reset_to_empty()
            self.save()
            return

        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                raw = f.read().strip()
        except OSError as exc:
            raise RuntimeError(f"Could not read data file: {exc}") from exc

        if not raw:
            # Empty file -- treat as a fresh planner instead of crashing.
            self._reset_to_empty()
            self.save()
            return

        try:
            payload = json.loads(raw)
            subjects = {
                int(s["id"]): Subject.from_dict(s) for s in payload.get("subjects", [])
            }
            tasks = {
                int(t["id"]): Task.from_dict(t) for t in payload.get("tasks", [])
            }
            next_subject_id = int(
                payload.get("next_subject_id", self._compute_next_id(subjects))
            )
            next_task_id = int(
                payload.get("next_task_id", self._compute_next_id(tasks))
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            # Corrupted data file: never overwrite it silently. Back it up
            # so the user can inspect/recover it, then start clean.
            backup_path = self._backup_corrupted_file()
            self._reset_to_empty()
            self.save()
            self.startup_warning = (
                "The saved data file was corrupted and could not be read.\n"
                f"A backup was saved to:\n{backup_path}\n\n"
                "Starting with a new, empty planner."
            )
            return

        self.subjects = subjects
        self.tasks = tasks
        self._next_subject_id = next_subject_id
        self._next_task_id = next_task_id

    def _backup_corrupted_file(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{self.filepath}.corrupted_{timestamp}.bak"
        shutil.copy2(self.filepath, backup_path)
        return backup_path

    def _reset_to_empty(self) -> None:
        self.subjects = {}
        self.tasks = {}
        self._next_subject_id = 1
        self._next_task_id = 1

    @staticmethod
    def _compute_next_id(items: dict) -> int:
        return (max(items.keys()) + 1) if items else 1

    def save(self) -> None:
        """Write the current in-memory state to disk."""
        payload = {
            "subjects": [s.to_dict() for s in self.subjects.values()],
            "tasks": [t.to_dict() for t in self.tasks.values()],
            "next_subject_id": self._next_subject_id,
            "next_task_id": self._next_task_id,
        }
        directory = os.path.dirname(self.filepath)
        if directory:
            os.makedirs(directory, exist_ok=True)

        # Write to a temp file then atomically replace, so a crash mid-write
        # can never leave the real data file half-written / corrupted.
        tmp_path = f"{self.filepath}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, self.filepath)

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def validate_date(value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValidationError("Deadline cannot be empty.")
        try:
            datetime.strptime(value, DATE_FORMAT)
        except ValueError:
            raise ValidationError(
                f"Deadline must be in {DATE_FORMAT} format, e.g. 2026-12-31."
            )
        return value

    @staticmethod
    def validate_priority(value: str) -> str:
        if value not in PRIORITIES:
            raise ValidationError(f"Priority must be one of: {', '.join(PRIORITIES)}.")
        return value

    @staticmethod
    def validate_status(value: str) -> str:
        if value not in STATUSES:
            raise ValidationError(f"Status must be one of: {', '.join(STATUSES)}.")
        return value

    @staticmethod
    def validate_title(value: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValidationError("Task title cannot be empty.")
        return value

    def validate_subject_name(self, value: str, *, ignore_id: Optional[int] = None) -> str:
        value = (value or "").strip()
        if not value:
            raise ValidationError("Subject name cannot be empty.")
        for subject in self.subjects.values():
            if subject.id != ignore_id and subject.name.lower() == value.lower():
                raise ValidationError(f"A subject named '{value}' already exists.")
        return value

    # ------------------------------------------------------------------
    # Subject CRUD
    # ------------------------------------------------------------------

    def add_subject(self, name: str, description: str = "") -> Subject:
        clean_name = self.validate_subject_name(name)
        subject = Subject(
            id=self._next_subject_id, name=clean_name, description=description.strip()
        )
        self.subjects[subject.id] = subject
        self._next_subject_id += 1
        self.save()
        return subject

    def edit_subject(self, subject_id: int, name: str, description: str = "") -> Subject:
        subject = self.get_subject(subject_id)
        if subject is None:
            raise ValidationError("Subject not found.")
        clean_name = self.validate_subject_name(name, ignore_id=subject_id)
        subject.name = clean_name
        subject.description = description.strip()
        self.save()
        return subject

    def delete_subject(self, subject_id: int) -> None:
        subject = self.get_subject(subject_id)
        if subject is None:
            raise ValidationError("Subject not found.")
        attached = [t for t in self.tasks.values() if t.subject_id == subject_id]
        if attached:
            raise SubjectHasTasksError(subject, len(attached))
        del self.subjects[subject_id]
        self.save()

    def get_subject(self, subject_id: int) -> Optional[Subject]:
        return self.subjects.get(subject_id)

    def get_subjects(self) -> list[Subject]:
        return sorted(self.subjects.values(), key=lambda s: s.name.lower())

    # ------------------------------------------------------------------
    # Task CRUD
    # ------------------------------------------------------------------

    def add_task(
        self,
        title: str,
        subject_id: int,
        priority: str,
        deadline: str,
        status: str,
        description: str = "",
    ) -> Task:
        clean_title = self.validate_title(title)
        if self.get_subject(subject_id) is None:
            raise ValidationError("Please select a valid subject.")
        clean_priority = self.validate_priority(priority)
        clean_deadline = self.validate_date(deadline)
        clean_status = self.validate_status(status)

        task = Task(
            id=self._next_task_id,
            title=clean_title,
            subject_id=subject_id,
            priority=clean_priority,
            deadline=clean_deadline,
            status=clean_status,
            description=description.strip(),
            created_at=date.today().strftime(DATE_FORMAT),
            completion_date=date.today().strftime(DATE_FORMAT) if clean_status == "Completed" else "",
        )
        self.tasks[task.id] = task
        self._next_task_id += 1
        self.save()
        return task

    def edit_task(
        self,
        task_id: int,
        title: str,
        subject_id: int,
        priority: str,
        deadline: str,
        status: str,
        description: str = "",
    ) -> Task:
        task = self.get_task(task_id)
        if task is None:
            raise ValidationError("Task not found.")
        if self.get_subject(subject_id) is None:
            raise ValidationError("Please select a valid subject.")

        # Validate everything before mutating so a bad field never leaves
        # the task half-updated.
        clean_title = self.validate_title(title)
        clean_priority = self.validate_priority(priority)
        clean_deadline = self.validate_date(deadline)
        clean_status = self.validate_status(status)

        task.title = clean_title
        task.subject_id = subject_id
        task.priority = clean_priority
        task.deadline = clean_deadline
        task.description = description.strip()
        self._apply_status(task, clean_status)
        self.save()
        return task

    def set_task_status(self, task_id: int, status: str) -> Task:
        """Quick status change (e.g. from a right-click menu) without a full edit."""
        task = self.get_task(task_id)
        if task is None:
            raise ValidationError("Task not found.")
        clean_status = self.validate_status(status)
        self._apply_status(task, clean_status)
        self.save()
        return task

    def _apply_status(self, task: Task, status: str) -> None:
        """
        Centralizes the status <-> completion_date relationship so both
        edit_task() and set_task_status() behave identically.

        - Moving TO Completed records today's date, unless a completion
          date is already recorded (editing an already-completed task
          shouldn't bump its completion date).
        - Moving AWAY from Completed clears the completion date -- Phase 2
          keeps this simple rather than preserving a completion history.
        """
        task.status = status
        if status == "Completed":
            if not task.completion_date:
                task.completion_date = date.today().strftime(DATE_FORMAT)
        else:
            task.completion_date = ""

    def delete_task(self, task_id: int) -> None:
        if task_id not in self.tasks:
            raise ValidationError("Task not found.")
        del self.tasks[task_id]
        self.save()

    def get_task(self, task_id: int) -> Optional[Task]:
        return self.tasks.get(task_id)

    def get_tasks(self) -> list[Task]:
        return sorted(self.tasks.values(), key=lambda t: (t.deadline, t.priority))

    # ------------------------------------------------------------------
    # Search / filter / sort (Phase 2)
    # ------------------------------------------------------------------

    _PRIORITY_RANK = {"High": 0, "Medium": 1, "Low": 2}
    _STATUS_RANK = {"Not Started": 0, "In Progress": 1, "Completed": 2}

    def _sort_key(self, task: Task, sort_by: str):
        if sort_by == "title":
            return task.title.lower()
        if sort_by == "subject":
            subject = self.get_subject(task.subject_id)
            return subject.name.lower() if subject else "\uffff"
        if sort_by == "priority":
            return self._PRIORITY_RANK.get(task.priority, 3)
        if sort_by == "status":
            return self._STATUS_RANK.get(task.status, 3)
        if sort_by == "created":
            return task.created_at or ""
        # default: deadline (ISO format sorts chronologically as text)
        return task.deadline or "9999-99-99"

    def deadline_category(self, task: Task, today: Optional[date] = None) -> str:
        """
        Classify a task's deadline relative to today, independent of its
        status -- a task can be "Not Started" and "overdue" at the same
        time. Completed tasks are never flagged as overdue/due-soon since
        there's nothing left to be late on.

        Returns one of: "completed", "none", "overdue", "today",
        "tomorrow", "upcoming" (2-7 days out), "later" (8+ days out).
        """
        if task.status == "Completed":
            return "completed"
        if not task.deadline:
            return "none"
        try:
            deadline_date = datetime.strptime(task.deadline, DATE_FORMAT).date()
        except ValueError:
            return "none"
        today = today or date.today()
        delta = (deadline_date - today).days
        if delta < 0:
            return "overdue"
        if delta == 0:
            return "today"
        if delta == 1:
            return "tomorrow"
        if delta <= 7:
            return "upcoming"
        return "later"

    def _deadline_matches(self, task: Task, filter_name: str, today: date) -> bool:
        if filter_name == "All":
            return True
        if not task.deadline:
            return False
        try:
            deadline_date = datetime.strptime(task.deadline, DATE_FORMAT).date()
        except ValueError:
            return False
        if filter_name == "Today":
            return deadline_date == today
        if filter_name == "This week":
            return today <= deadline_date <= today + timedelta(days=7)
        if filter_name == "Overdue":
            return deadline_date < today and task.status != "Completed"
        if filter_name == "Upcoming":
            return deadline_date > today
        # Unknown/invalid filter value: don't filter anything out.
        return True

    def get_tasks_filtered(
        self,
        search: str = "",
        status: str = "All",
        priority: str = "All",
        subject_id: Optional[int] = None,
        deadline_filter: str = "All",
        exclude_completed: bool = False,
        sort_by: str = "deadline",
        reverse: bool = False,
        today: Optional[date] = None,
    ) -> list[Task]:
        """
        Single source of truth for the Tasks tab, the Upcoming/Overdue
        view, and any future consumer that needs a filtered task list.
        Never mutates stored data or task order -- it only returns a new
        list, sorted for display.
        """
        today = today or date.today()
        search = (search or "").strip().lower()
        results = []
        for task in self.tasks.values():
            if exclude_completed and task.status == "Completed":
                continue
            if status != "All" and task.status != status:
                continue
            if priority != "All" and task.priority != priority:
                continue
            if subject_id is not None and task.subject_id != subject_id:
                continue
            if not self._deadline_matches(task, deadline_filter, today):
                continue
            if search:
                subject = self.get_subject(task.subject_id)
                subject_name = subject.name.lower() if subject else ""
                haystack = f"{task.title} {task.description} {subject_name}".lower()
                if search not in haystack:
                    continue
            results.append(task)
        results.sort(key=lambda t: self._sort_key(t, sort_by), reverse=reverse)
        return results

    def get_tasks_by_date(self, date_str: str) -> list[Task]:
        """All tasks whose deadline exactly matches date_str (YYYY-MM-DD)."""
        matches = [t for t in self.tasks.values() if t.deadline == date_str]
        matches.sort(key=lambda t: self._sort_key(t, "priority"))
        return matches

    def get_task_days_in_month(self, year: int, month: int) -> set[int]:
        """Day-of-month numbers (1-31) that have at least one task deadline."""
        prefix = f"{year:04d}-{month:02d}-"
        days = set()
        for task in self.tasks.values():
            if task.deadline.startswith(prefix):
                try:
                    days.add(int(task.deadline[8:10]))
                except ValueError:
                    continue
        return days

    # ------------------------------------------------------------------
    # Dashboard summary
    # ------------------------------------------------------------------

    def get_summary(self) -> dict:
        today = date.today()
        total = len(self.tasks)
        completed = sum(1 for t in self.tasks.values() if t.status == "Completed")
        in_progress = sum(1 for t in self.tasks.values() if t.status == "In Progress")
        not_started = sum(1 for t in self.tasks.values() if t.status == "Not Started")
        overdue = sum(1 for t in self.tasks.values() if self.deadline_category(t, today) == "overdue")
        due_today = sum(1 for t in self.tasks.values() if self.deadline_category(t, today) == "today")
        return {
            "total": total,
            "completed": completed,
            "in_progress": in_progress,
            "not_started": not_started,
            "overdue": overdue,
            "due_today": due_today,
        }

    def get_subject_summary(self, subject_id: int) -> dict:
        """Task counts for one subject, used by the Subjects tab."""
        tasks = [t for t in self.tasks.values() if t.subject_id == subject_id]
        completed = sum(1 for t in tasks if t.status == "Completed")
        return {"total": len(tasks), "completed": completed, "pending": len(tasks) - completed}
