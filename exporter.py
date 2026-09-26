"""
Export (CSV + text summary report) and backup/restore.

Everything here only *reads* DataManager state, except restore_backup(),
which goes through DataManager.replace_data() so validation and saving
stay in one place. Exports are written wherever the user chooses and
never touch the app's own data file.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import date, datetime
from typing import Optional

from data_manager import DataManager, write_json_atomic
from validation import ParsedData, PayloadError, parse_payload

BACKUP_EXTENSION = ".json"


def _subject_name(dm: DataManager, subject_id: int) -> str:
    subject = dm.get_subject(subject_id)
    return subject.name if subject else "(deleted subject)"


def export_tasks_csv(dm: DataManager, path: str) -> int:
    """Write all tasks to CSV; returns the number of rows written."""
    tasks = dm.get_tasks_filtered(sort_by="deadline")
    # utf-8-sig so Excel detects the encoding and shows non-ASCII titles correctly.
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "Title", "Subject", "Priority", "Deadline", "Status",
                         "Created", "Completed", "Description"])
        for t in tasks:
            writer.writerow([t.id, t.title, _subject_name(dm, t.subject_id), t.priority, t.deadline,
                             t.status, t.created_at, t.completion_date, t.description])
    return len(tasks)


def export_sessions_csv(dm: DataManager, path: str) -> int:
    sessions = dm.get_sessions()
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["ID", "Date", "Subject", "Task", "Duration (minutes)", "Notes"])
        for s in sessions:
            if s.task_id is None:
                task_title = ""
            else:
                task = dm.get_task(s.task_id)
                task_title = task.title if task else "(deleted task)"
            writer.writerow([s.id, s.date, _subject_name(dm, s.subject_id), task_title,
                             s.duration_minutes, s.notes])
    return len(sessions)


def build_summary_report(dm: DataManager, today: Optional[date] = None) -> str:
    """Plain-text summary built entirely from the same DataManager
    calculations the dashboard and Analytics tab use."""
    today = today or date.today()
    fmt = dm.format_duration
    tasks = dm.get_summary(today)
    study = dm.get_study_time_summary(today)
    by_subject = dm.get_study_minutes_by_subject("All time", today)
    pending = tasks["not_started"] + tasks["in_progress"]

    lines = [
        "STUDY PLANNER SUMMARY REPORT",
        f"Generated: {today.isoformat()}",
        "",
        "TASKS",
        f"  Total:         {tasks['total']}",
        f"  Completed:     {tasks['completed']} ({tasks['completion_pct']:.1f}%)",
        f"  Pending:       {pending} (In Progress: {tasks['in_progress']}, Not Started: {tasks['not_started']})",
        f"  Overdue:       {tasks['overdue']}",
        "",
        "STUDY TIME",
        f"  Total:         {fmt(study['total'])}",
        f"  This month:    {fmt(study['this_month'])}",
        f"  This week:     {fmt(study['this_week'])}",
        f"  Today:         {fmt(study['today'])}",
        f"  Most studied:  {by_subject[0][0] if by_subject else 'n/a'}",
        "",
        "STUDY TIME BY SUBJECT",
    ]
    if by_subject:
        lines += [f"  {name:<24} {fmt(minutes)}" for name, minutes in by_subject]
    else:
        lines.append("  No study sessions recorded.")

    lines += ["", "TASK COMPLETION BY SUBJECT"]
    subjects = dm.get_subjects()
    if subjects:
        for subject in subjects:
            s = dm.get_subject_summary(subject.id, today=today)
            lines.append(f"  {subject.name:<24} {s['completed']}/{s['total']} ({s['completion_pct']:.1f}%)")
    else:
        lines.append("  No subjects yet.")
    return "\n".join(lines) + "\n"


def export_summary_report(dm: DataManager, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_summary_report(dm))


# ----------------------------------------------------------------------
# Backup / restore
# ----------------------------------------------------------------------

def default_backup_name() -> str:
    return f"study_planner_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}{BACKUP_EXTENSION}"


def create_backup(dm: DataManager, path: str) -> None:
    """A backup is the planner data in exactly the on-disk format, so a
    backup file is also a valid data file."""
    write_json_atomic(path, dm.to_payload())


def load_backup(path: str) -> tuple[dict, ParsedData]:
    """
    Read and fully validate a backup WITHOUT touching current data.
    Returns (payload, parsed) so the caller can show what it contains
    before asking for confirmation. Raises OSError for unreadable files
    and PayloadError for anything that isn't a clean planner backup.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PayloadError(f"The file is not valid JSON ({exc}).") from exc
    if not isinstance(payload, dict) or not {"subjects", "tasks"} <= payload.keys():
        raise PayloadError("The file is not a Study Planner backup (missing subjects/tasks).")
    parsed = parse_payload(payload)
    if parsed.issues:
        raise PayloadError(
            "The backup contains invalid records:\n"
            + "\n".join(f"- {issue}" for issue in parsed.issues[:10])
        )
    return payload, parsed


def restore_backup(dm: DataManager, payload: dict, safety_dir: str) -> str:
    """
    Replace planner data with an already-validated backup payload. The
    current data file is first copied into `safety_dir` so a restore can
    itself be undone; returns that safety-copy path.
    """
    os.makedirs(safety_dir, exist_ok=True)
    safety_path = os.path.join(
        safety_dir, f"before_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}{BACKUP_EXTENSION}"
    )
    create_backup(dm, safety_path)
    dm.replace_data(payload)
    return safety_path
