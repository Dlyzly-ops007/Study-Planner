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

from models import Subject, Task, StudySession, PRIORITIES, STATUSES, DATE_FORMAT
from validation import ParsedData, PayloadError, parse_payload


def write_json_atomic(path: str, payload) -> None:
    """Write to a temp file then atomically replace, so a crash mid-write
    can never leave the real file half-written / corrupted."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_path, path)


class ValidationError(Exception):
    """Raised when user-supplied data fails validation. Message is user-facing."""


class StorageError(Exception):
    """Raised when the data file can't be read or written. Message is user-facing."""


class SubjectHasTasksError(Exception):
    """
    Raised when trying to delete a subject that still has tasks and/or
    study sessions attached.

    Deletion policy (unchanged in spirit since Phase 1, extended in
    Phase 3 to cover sessions too): a subject that's still referenced by
    anything is never deleted automatically, and nothing is ever
    silently destroyed. The caller (the GUI) tells the user what's
    still attached and asks them to clean it up first.
    """

    def __init__(self, subject: Subject, task_count: int, session_count: int = 0):
        self.subject = subject
        self.task_count = task_count
        self.session_count = session_count
        parts = []
        if task_count:
            parts.append(f"{task_count} task(s)")
        if session_count:
            parts.append(f"{session_count} study session(s)")
        super().__init__(f"Subject '{subject.name}' still has {' and '.join(parts)} attached.")


class DataManager:
    """Loads, validates, mutates, and saves the planner's data file."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.subjects: dict[int, Subject] = {}
        self.tasks: dict[int, Task] = {}
        self.sessions: dict[int, StudySession] = {}
        self._next_subject_id = 1
        self._next_task_id = 1
        self._next_session_id = 1
        # Set by load() if the existing data file was corrupted and had to
        # be reset. main.py surfaces this to the user via a message box.
        self.startup_warning: Optional[str] = None
        self.load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load data from disk, creating an empty file if none exists yet.

        Never silently discards data: if the file can't be parsed, or some
        records had to be repaired/skipped, the original file is copied
        aside first and startup_warning explains what happened.
        """
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
        except (OSError, UnicodeDecodeError) as exc:
            raise StorageError(f"Could not read data file:\n{self.filepath}\n\n{exc}") from exc

        if not raw:
            # Empty file -- treat as a fresh planner instead of crashing.
            self._reset_to_empty()
            self.save()
            return

        try:
            parsed = parse_payload(json.loads(raw))
        except (json.JSONDecodeError, PayloadError):
            # Corrupted data file: never overwrite it silently. Back it up
            # so the user can inspect/recover it, then start clean.
            backup_path = self._backup_original_file("corrupted")
            self._reset_to_empty()
            self.save()
            self.startup_warning = (
                "The saved data file was corrupted and could not be read.\n"
                f"The original was preserved at:\n{backup_path}\n\n"
                "Starting with a new, empty planner. If you have a backup, "
                "use File > Restore from Backup."
            )
            return

        self._apply_parsed(parsed)
        if parsed.issues:
            backup_path = self._backup_original_file("before_repair")
            shown = "\n".join(f"- {issue}" for issue in parsed.issues[:10])
            more = len(parsed.issues) - 10
            if more > 0:
                shown += f"\n...and {more} more."
            self.startup_warning = (
                "Some saved data was invalid and has been repaired:\n\n"
                f"{shown}\n\nThe original file was preserved at:\n{backup_path}"
            )
            self.save()

    def _apply_parsed(self, parsed: ParsedData) -> None:
        self.subjects = parsed.subjects
        self.tasks = parsed.tasks
        self.sessions = parsed.sessions
        self._next_subject_id = parsed.next_subject_id
        self._next_task_id = parsed.next_task_id
        self._next_session_id = parsed.next_session_id

    def _backup_original_file(self, reason: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{self.filepath}.{reason}_{timestamp}.bak"
        shutil.copy2(self.filepath, backup_path)
        return backup_path

    def _reset_to_empty(self) -> None:
        self._apply_parsed(ParsedData())

    def to_payload(self) -> dict:
        """The exact structure written to disk (also the backup format)."""
        return {
            "subjects": [s.to_dict() for s in self.subjects.values()],
            "tasks": [t.to_dict() for t in self.tasks.values()],
            "study_sessions": [s.to_dict() for s in self.sessions.values()],
            "next_subject_id": self._next_subject_id,
            "next_task_id": self._next_task_id,
            "next_session_id": self._next_session_id,
        }

    def save(self) -> None:
        """Write the current in-memory state to disk."""
        try:
            write_json_atomic(self.filepath, self.to_payload())
        except OSError as exc:
            raise StorageError(f"Could not save your data:\n{exc}") from exc

    def replace_data(self, payload: dict) -> None:
        """Replace all planner data (used by restore). Strict: raises
        PayloadError if the payload needed ANY repair, leaving current data
        untouched -- a restore should never quietly lose records."""
        parsed = parse_payload(payload)
        if parsed.issues:
            raise PayloadError(
                "The backup contains invalid records:\n"
                + "\n".join(f"- {issue}" for issue in parsed.issues[:10])
            )
        previous = self.to_payload()
        self._apply_parsed(parsed)
        try:
            self.save()
        except StorageError:
            self._apply_parsed(parse_payload(previous))
            raise

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def validate_date(value: str, label: str = "Deadline") -> str:
        value = (value or "").strip()
        if not value:
            raise ValidationError(f"{label} cannot be empty.")
        try:
            datetime.strptime(value, DATE_FORMAT)
        except ValueError:
            raise ValidationError(
                f"{label} must be a real date in YYYY-MM-DD format, e.g. 2026-12-31."
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

    @staticmethod
    def validate_duration(value) -> int:
        """Accepts an int or a numeric string; must be a positive whole number of minutes."""
        try:
            minutes = int(str(value).strip())
        except (TypeError, ValueError):
            raise ValidationError("Duration must be a whole number of minutes.")
        if minutes <= 0:
            raise ValidationError("Duration must be greater than zero.")
        return minutes

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
        attached_tasks = [t for t in self.tasks.values() if t.subject_id == subject_id]
        attached_sessions = [s for s in self.sessions.values() if s.subject_id == subject_id]
        if attached_tasks or attached_sessions:
            raise SubjectHasTasksError(subject, len(attached_tasks), len(attached_sessions))
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
        # Study sessions may reference this task; we intentionally do NOT
        # block or cascade here (see SessionDialog / get_task in gui.py --
        # an orphaned session simply displays "(deleted task)"). A task is
        # often deleted long after it's done, and its study history is
        # still worth keeping.
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
    # Study session CRUD (Phase 3)
    # ------------------------------------------------------------------

    def add_session(
        self,
        subject_id: int,
        date_str: str,
        duration_minutes,
        task_id: Optional[int] = None,
        notes: str = "",
    ) -> StudySession:
        clean_subject_id, clean_task_id = self._validate_session_links(subject_id, task_id)
        clean_date = self.validate_date(date_str, "Date")
        clean_duration = self.validate_duration(duration_minutes)

        session = StudySession(
            id=self._next_session_id,
            subject_id=clean_subject_id,
            date=clean_date,
            duration_minutes=clean_duration,
            task_id=clean_task_id,
            notes=notes.strip(),
        )
        self.sessions[session.id] = session
        self._next_session_id += 1
        self.save()
        return session

    def edit_session(
        self,
        session_id: int,
        subject_id: int,
        date_str: str,
        duration_minutes,
        task_id: Optional[int] = None,
        notes: str = "",
    ) -> StudySession:
        session = self.get_session(session_id)
        if session is None:
            raise ValidationError("Study session not found.")
        clean_subject_id, clean_task_id = self._validate_session_links(subject_id, task_id)
        clean_date = self.validate_date(date_str, "Date")
        clean_duration = self.validate_duration(duration_minutes)

        session.subject_id = clean_subject_id
        session.task_id = clean_task_id
        session.date = clean_date
        session.duration_minutes = clean_duration
        session.notes = notes.strip()
        self.save()
        return session

    def _validate_session_links(
        self, subject_id: int, task_id: Optional[int]
    ) -> tuple[int, Optional[int]]:
        if self.get_subject(subject_id) is None:
            raise ValidationError("Please select a valid subject.")
        if task_id is None:
            return subject_id, None
        task = self.get_task(task_id)
        if task is None:
            raise ValidationError("Selected task no longer exists.")
        if task.subject_id != subject_id:
            raise ValidationError("The selected task does not belong to the selected subject.")
        return subject_id, task_id

    def delete_session(self, session_id: int) -> None:
        if session_id not in self.sessions:
            raise ValidationError("Study session not found.")
        del self.sessions[session_id]
        self.save()

    def get_session(self, session_id: int) -> Optional[StudySession]:
        return self.sessions.get(session_id)

    def get_sessions(self) -> list[StudySession]:
        return sorted(self.sessions.values(), key=lambda s: (s.date, s.id), reverse=True)

    # ------------------------------------------------------------------
    # Date-period filtering (reusable across sessions and stats)
    # ------------------------------------------------------------------

    PERIODS: tuple[str, ...] = ("Today", "This week", "This month", "All time")

    def _period_bounds(
        self, period: str, today: Optional[date] = None
    ) -> tuple[Optional[date], Optional[date]]:
        """Inclusive (start, end) for a named period, or (None, None) for All time."""
        today = today or date.today()
        if period == "Today":
            return today, today
        if period == "This week":
            start = today - timedelta(days=today.weekday())  # Monday
            return start, today
        if period == "This month":
            return today.replace(day=1), today
        return None, None

    def _session_date(self, session: StudySession) -> Optional[date]:
        try:
            return datetime.strptime(session.date, DATE_FORMAT).date()
        except (ValueError, TypeError):
            return None

    def get_sessions_filtered(
        self,
        period: str = "All time",
        subject_id: Optional[int] = None,
        today: Optional[date] = None,
    ) -> list[StudySession]:
        """
        Single source of truth for period + subject filtering of sessions,
        used by the Study Sessions tab, the dashboard, and analytics alike.
        Never mutates stored data or order.
        """
        start, end = self._period_bounds(period, today)
        results = []
        for session in self.sessions.values():
            if subject_id is not None and session.subject_id != subject_id:
                continue
            if start is not None:
                session_date = self._session_date(session)
                if session_date is None or not (start <= session_date <= end):
                    continue
            results.append(session)
        results.sort(key=lambda s: (s.date, s.id), reverse=True)
        return results

    # ------------------------------------------------------------------
    # Study-duration calculations (Phase 3)
    # ------------------------------------------------------------------

    @staticmethod
    def total_minutes(sessions: list[StudySession]) -> int:
        return sum(s.duration_minutes for s in sessions)

    @staticmethod
    def format_duration(minutes: int) -> str:
        """90 -> '1h 30m'; 45 -> '45m'; 120 -> '2h'; avoids decimal hours."""
        minutes = max(0, int(minutes))
        hours, mins = divmod(minutes, 60)
        if hours and mins:
            return f"{hours}h {mins}m"
        if hours:
            return f"{hours}h"
        return f"{mins}m"

    def get_study_time_summary(self, today: Optional[date] = None) -> dict:
        today = today or date.today()
        return {
            "total": self.total_minutes(list(self.sessions.values())),
            "today": self.total_minutes(self.get_sessions_filtered("Today", today=today)),
            "this_week": self.total_minutes(self.get_sessions_filtered("This week", today=today)),
            "this_month": self.total_minutes(self.get_sessions_filtered("This month", today=today)),
        }

    def get_task_study_minutes(self, task_id: int) -> int:
        return sum(s.duration_minutes for s in self.sessions.values() if s.task_id == task_id)

    # ------------------------------------------------------------------
    # Subject / task / productivity statistics (Phase 3)
    # ------------------------------------------------------------------

    def get_subject_summary(
        self, subject_id: int, period: str = "All time", today: Optional[date] = None
    ) -> dict:
        """
        Per-subject task + study-time progress. Everything here is computed
        live from self.tasks / self.sessions -- there is no separately
        stored "progress" value to keep in sync.
        """
        today = today or date.today()
        tasks = [t for t in self.tasks.values() if t.subject_id == subject_id]
        total = len(tasks)
        completed = sum(1 for t in tasks if t.status == "Completed")
        pending = total - completed
        overdue = sum(1 for t in tasks if self.deadline_category(t, today) == "overdue")
        completion_pct = round((completed / total) * 100, 1) if total else 0.0
        study_minutes = self.total_minutes(
            self.get_sessions_filtered(period=period, subject_id=subject_id, today=today)
        )
        return {
            "total": total,
            "completed": completed,
            "pending": pending,
            "overdue": overdue,
            "completion_pct": completion_pct,
            "study_minutes": study_minutes,
        }

    def get_productivity_summary(self, period: str = "This week", today: Optional[date] = None) -> dict:
        today = today or date.today()
        sessions = self.get_sessions_filtered(period=period, today=today)
        session_count = len(sessions)
        study_minutes = self.total_minutes(sessions)
        avg_minutes = round(study_minutes / session_count) if session_count else 0

        start, end = self._period_bounds(period, today)
        tasks_completed = 0
        for task in self.tasks.values():
            if task.status != "Completed" or not task.completion_date:
                continue
            try:
                c_date = datetime.strptime(task.completion_date, DATE_FORMAT).date()
            except ValueError:
                continue
            if start is None or (start <= c_date <= end):
                tasks_completed += 1

        most_studied_subject = self._top_subject_by_minutes(sessions)

        return {
            "tasks_completed": tasks_completed,
            "study_minutes": study_minutes,
            "session_count": session_count,
            "avg_session_minutes": avg_minutes,
            "most_studied_subject": most_studied_subject,
        }

    def _top_subject_by_minutes(self, sessions: list[StudySession]) -> Optional[str]:
        minutes_by_subject: dict[int, int] = {}
        for session in sessions:
            minutes_by_subject[session.subject_id] = (
                minutes_by_subject.get(session.subject_id, 0) + session.duration_minutes
            )
        if not minutes_by_subject:
            return None
        top_id = max(minutes_by_subject, key=minutes_by_subject.get)
        subject = self.get_subject(top_id)
        return subject.name if subject else "(deleted subject)"

    def get_dashboard_summary(self, today: Optional[date] = None) -> dict:
        """
        The upgraded Phase 3 dashboard: task summary + study-time summary +
        two subject highlights, all derived from existing data (nothing
        here is a separately maintained value).
        """
        today = today or date.today()
        task_summary = self.get_summary(today)
        study_summary = self.get_study_time_summary(today=today)
        most_studied = self._top_subject_by_minutes(list(self.sessions.values()))

        best_subject_name = None
        best_pct = -1.0
        for subject in self.subjects.values():
            stats = self.get_subject_summary(subject.id, today=today)
            if stats["total"] == 0:
                continue
            if stats["completion_pct"] > best_pct:
                best_pct = stats["completion_pct"]
                best_subject_name = subject.name

        return {
            "task": task_summary,
            "study": study_summary,
            "most_studied_subject": most_studied,
            "best_completion_subject": best_subject_name,
            "best_completion_pct": best_pct if best_subject_name else None,
        }

    # ------------------------------------------------------------------
    # Chart-ready data for the Analytics tab (no plotting logic here --
    # gui.py owns rendering, this just returns the numbers).
    # ------------------------------------------------------------------

    def get_study_minutes_by_subject(
        self, period: str = "All time", today: Optional[date] = None
    ) -> list[tuple[str, int]]:
        sessions = self.get_sessions_filtered(period=period, today=today)
        minutes_by_subject: dict[int, int] = {}
        for session in sessions:
            minutes_by_subject[session.subject_id] = (
                minutes_by_subject.get(session.subject_id, 0) + session.duration_minutes
            )
        by_name: dict[str, int] = {}
        for subject_id, minutes in minutes_by_subject.items():
            subject = self.get_subject(subject_id)
            name = subject.name if subject else "(deleted subject)"
            by_name[name] = by_name.get(name, 0) + minutes
        return sorted(by_name.items(), key=lambda item: item[1], reverse=True)

    def get_study_minutes_by_day(
        self, period: str = "This week", today: Optional[date] = None
    ) -> list[tuple[str, int]]:
        """[(YYYY-MM-DD, minutes), ...] zero-filled across the period so the
        line chart shows gaps instead of silently skipping empty days."""
        today = today or date.today()
        start, end = self._period_bounds(period, today)
        if start is None:
            session_dates = [d for d in (self._session_date(s) for s in self.sessions.values()) if d]
            if not session_dates:
                return []
            start, end = min(session_dates), max(session_dates)

        minutes_by_date: dict[str, int] = {}
        for session in self.sessions.values():
            session_date = self._session_date(session)
            if session_date is None or not (start <= session_date <= end):
                continue
            minutes_by_date[session.date] = minutes_by_date.get(session.date, 0) + session.duration_minutes

        days = []
        cursor = start
        while cursor <= end:
            key = cursor.strftime(DATE_FORMAT)
            days.append((key, minutes_by_date.get(key, 0)))
            cursor += timedelta(days=1)
        return days

    def get_task_completion_by_subject(self) -> list[tuple[str, int, int]]:
        """[(subject_name, completed, total), ...] for every subject, alphabetical."""
        return [
            (
                subject.name,
                sum(1 for t in self.tasks.values() if t.subject_id == subject.id and t.status == "Completed"),
                sum(1 for t in self.tasks.values() if t.subject_id == subject.id),
            )
            for subject in self.get_subjects()
        ]

    # ------------------------------------------------------------------
    # Dashboard summary
    # ------------------------------------------------------------------

    def get_summary(self, today: Optional[date] = None) -> dict:
        today = today or date.today()
        total = len(self.tasks)
        completed = sum(1 for t in self.tasks.values() if t.status == "Completed")
        in_progress = sum(1 for t in self.tasks.values() if t.status == "In Progress")
        not_started = sum(1 for t in self.tasks.values() if t.status == "Not Started")
        overdue = sum(1 for t in self.tasks.values() if self.deadline_category(t, today) == "overdue")
        due_today = sum(1 for t in self.tasks.values() if self.deadline_category(t, today) == "today")
        return {
            "completion_pct": round((completed / total) * 100, 1) if total else 0.0,
            "total": total,
            "completed": completed,
            "in_progress": in_progress,
            "not_started": not_started,
            "overdue": overdue,
            "due_today": due_today,
        }
