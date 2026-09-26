"""
Deadline reminders.

Deliberately lightweight: reminders are computed on demand from the task
list (reusing DataManager.deadline_category, so "overdue"/"today" mean
exactly what they mean everywhere else in the app) and shown inside the
app -- no background service, no OS-notification dependency.

ReminderTracker remembers which (task, category) pairs the user has
already been alerted about during this run, so the periodic check never
pops up the same reminder twice.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from data_manager import DataManager
from models import DATE_FORMAT, Task

GROUPS = ("overdue", "today", "tomorrow", "soon")
GROUP_LABELS = {
    "overdue": "Overdue",
    "today": "Due today",
    "tomorrow": "Due tomorrow",
    "soon": "Due soon",
}


def get_reminders(dm: DataManager, window_days: int, today: Optional[date] = None) -> dict[str, list[Task]]:
    """Open tasks grouped as overdue / today / tomorrow / soon (2..window_days out)."""
    today = today or date.today()
    groups: dict[str, list[Task]] = {g: [] for g in GROUPS}
    for task in dm.get_tasks_filtered(exclude_completed=True, sort_by="deadline", today=today):
        category = dm.deadline_category(task, today)
        if category in ("overdue", "today", "tomorrow"):
            groups[category].append(task)
        elif category in ("upcoming", "later"):
            days_left = (datetime.strptime(task.deadline, DATE_FORMAT).date() - today).days
            if days_left <= window_days:
                groups["soon"].append(task)
    return groups


def summary_line(groups: dict[str, list[Task]]) -> str:
    parts = [f"{GROUP_LABELS[g]}: {len(groups[g])}" for g in GROUPS if groups[g]]
    return "   ".join(parts) if parts else "No upcoming deadlines."


def format_details(dm: DataManager, groups: dict[str, list[Task]]) -> str:
    lines = []
    for g in GROUPS:
        if not groups[g]:
            continue
        lines.append(f"{GROUP_LABELS[g]}:")
        for task in groups[g]:
            subject = dm.get_subject(task.subject_id)
            lines.append(f"  - {task.title} ({subject.name if subject else '(deleted)'}, {task.deadline})")
        lines.append("")
    return "\n".join(lines).strip() or "No upcoming deadlines."


class ReminderTracker:
    """Filters reminder groups down to what hasn't been shown yet this run."""

    def __init__(self):
        self._shown: set[tuple[int, str]] = set()

    def new_items(self, groups: dict[str, list[Task]]) -> dict[str, list[Task]]:
        fresh = {g: [t for t in groups[g] if (t.id, g) not in self._shown] for g in GROUPS}
        for g, tasks in fresh.items():
            self._shown.update((t.id, g) for t in tasks)
        return fresh
