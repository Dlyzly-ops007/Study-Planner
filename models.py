"""
Data models for the Study Planner application.

This module only defines *data representations* for Subjects and Tasks.
Validation logic lives in data_manager.py and GUI logic lives in gui.py --
keeping them separate makes each piece easy to test and reason about on
its own.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

# Allowed values kept in one place so every part of the app agrees on them.
PRIORITIES: tuple[str, ...] = ("Low", "Medium", "High")
STATUSES: tuple[str, ...] = ("Not Started", "In Progress", "Completed")

DATE_FORMAT = "%Y-%m-%d"


@dataclass
class Subject:
    """A subject/course that tasks can belong to."""

    id: int
    name: str
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "Subject":
        return Subject(
            id=int(data["id"]),
            name=str(data["name"]),
            description=str(data.get("description", "")),
        )


@dataclass
class Task:
    """A single study task tied to a subject."""

    id: int
    title: str
    subject_id: int
    priority: str = "Medium"
    deadline: str = ""       # stored as a YYYY-MM-DD string
    status: str = "Not Started"
    description: str = ""
    created_at: str = ""     # YYYY-MM-DD string, set when the task is created
    completion_date: str = ""  # YYYY-MM-DD string, set when status becomes Completed

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "Task":
        return Task(
            id=int(data["id"]),
            title=str(data["title"]),
            subject_id=int(data["subject_id"]),
            priority=str(data.get("priority", "Medium")),
            deadline=str(data.get("deadline", "")),
            status=str(data.get("status", "Not Started")),
            description=str(data.get("description", "")),
            created_at=str(data.get("created_at", "")),
            # .get() with a default means Phase 1 data files (which predate
            # this field) load fine -- completed tasks saved before Phase 2
            # simply show no completion date until edited again.
            completion_date=str(data.get("completion_date", "")),
        )
