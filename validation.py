"""
Structural validation for planner data read from disk (the main data file
or a backup being restored).

parse_payload() never raises for bad *records* -- it repairs what can be
safely repaired (e.g. an unknown priority becomes "Medium"), drops records
that can't be used at all (e.g. no usable id), and reports every change
as a human-readable issue. The caller decides what to do with the issues:
DataManager.load() keeps the repaired data but preserves the original
file first; a restore rejects a backup that needed any repair.

It only raises PayloadError when the payload as a whole is unusable
(not a JSON object, or the top-level lists are the wrong type).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from models import DATE_FORMAT, PRIORITIES, STATUSES, StudySession, Subject, Task


class PayloadError(Exception):
    """The payload's overall structure is unusable. Message is user-facing."""


@dataclass
class ParsedData:
    subjects: dict[int, Subject] = field(default_factory=dict)
    tasks: dict[int, Task] = field(default_factory=dict)
    sessions: dict[int, StudySession] = field(default_factory=dict)
    next_subject_id: int = 1
    next_task_id: int = 1
    next_session_id: int = 1
    issues: list[str] = field(default_factory=list)


def is_valid_date(value) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.strptime(value, DATE_FORMAT)
    except ValueError:
        return False
    return True


def _as_int(value):
    """int(value) for ints and numeric strings; None for anything else (incl. bool)."""
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _get_list(payload: dict, key: str) -> list:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise PayloadError(f"'{key}' should be a list.")
    return value


def _next_id(payload: dict, key: str, items: dict) -> int:
    """Stored next-id counter, but never lower than max(existing id) + 1 --
    a stale counter would otherwise hand out an id that's already used."""
    minimum = (max(items) + 1) if items else 1
    stored = _as_int(payload.get(key))
    return max(stored, minimum) if stored is not None else minimum


def parse_payload(payload) -> ParsedData:
    if not isinstance(payload, dict):
        raise PayloadError("The data is not a planner file (expected a JSON object).")

    result = ParsedData()
    issues = result.issues

    for i, raw in enumerate(_get_list(payload, "subjects"), start=1):
        sid = _as_int(raw.get("id")) if isinstance(raw, dict) else None
        name = str(raw.get("name", "")).strip() if isinstance(raw, dict) else ""
        if sid is None or not name:
            issues.append(f"Subject #{i}: missing id or name -- skipped.")
            continue
        if sid in result.subjects:
            issues.append(f"Subject id {sid}: duplicate id -- later copy skipped.")
            continue
        result.subjects[sid] = Subject(id=sid, name=name, description=str(raw.get("description", "")))

    for i, raw in enumerate(_get_list(payload, "tasks"), start=1):
        if not isinstance(raw, dict):
            issues.append(f"Task #{i}: not a valid record -- skipped.")
            continue
        tid = _as_int(raw.get("id"))
        subject_id = _as_int(raw.get("subject_id"))
        title = str(raw.get("title", "")).strip()
        if tid is None or subject_id is None or not title:
            issues.append(f"Task #{i}: missing id, title, or subject -- skipped.")
            continue
        if tid in result.tasks:
            issues.append(f"Task id {tid}: duplicate id -- later copy skipped.")
            continue
        task = Task(
            id=tid, title=title, subject_id=subject_id,
            priority=str(raw.get("priority", "Medium")),
            deadline=str(raw.get("deadline", "") or ""),
            status=str(raw.get("status", "Not Started")),
            description=str(raw.get("description", "") or ""),
            created_at=str(raw.get("created_at", "") or ""),
            completion_date=str(raw.get("completion_date", "") or ""),
        )
        label = f"Task '{title}'"
        if task.priority not in PRIORITIES:
            issues.append(f"{label}: invalid priority '{task.priority}' -- set to Medium.")
            task.priority = "Medium"
        if task.status not in STATUSES:
            issues.append(f"{label}: invalid status '{task.status}' -- set to Not Started.")
            task.status = "Not Started"
        if task.deadline and not is_valid_date(task.deadline):
            issues.append(f"{label}: invalid deadline '{task.deadline}' -- cleared (edit the task to set one).")
            task.deadline = ""
        if task.created_at and not is_valid_date(task.created_at):
            task.created_at = ""
        if task.completion_date and not is_valid_date(task.completion_date):
            task.completion_date = ""
        if task.status != "Completed" and task.completion_date:
            task.completion_date = ""
        if subject_id not in result.subjects:
            # Kept, not dropped: the GUI shows it as "(deleted)" and the user
            # can reassign it to a real subject by editing it.
            issues.append(f"{label}: refers to a missing subject (id {subject_id}).")
        result.tasks[tid] = task

    for i, raw in enumerate(_get_list(payload, "study_sessions"), start=1):
        if not isinstance(raw, dict):
            issues.append(f"Study session #{i}: not a valid record -- skipped.")
            continue
        sid = _as_int(raw.get("id"))
        subject_id = _as_int(raw.get("subject_id"))
        duration = _as_int(raw.get("duration_minutes"))
        session_date = raw.get("date")
        if sid is None or subject_id is None:
            issues.append(f"Study session #{i}: missing id or subject -- skipped.")
            continue
        if sid in result.sessions:
            issues.append(f"Study session id {sid}: duplicate id -- later copy skipped.")
            continue
        if duration is None or duration <= 0:
            issues.append(f"Study session id {sid}: invalid duration -- skipped.")
            continue
        if not is_valid_date(session_date):
            issues.append(f"Study session id {sid}: invalid date -- skipped.")
            continue
        raw_task_id = raw.get("task_id")
        task_id = _as_int(raw_task_id) if raw_task_id not in (None, "") else None
        if subject_id not in result.subjects:
            issues.append(f"Study session id {sid}: refers to a missing subject (id {subject_id}).")
        result.sessions[sid] = StudySession(
            id=sid, subject_id=subject_id, date=session_date, duration_minutes=duration,
            task_id=task_id, notes=str(raw.get("notes", "") or ""),
        )

    result.next_subject_id = _next_id(payload, "next_subject_id", result.subjects)
    result.next_task_id = _next_id(payload, "next_task_id", result.tasks)
    result.next_session_id = _next_id(payload, "next_session_id", result.sessions)
    return result
