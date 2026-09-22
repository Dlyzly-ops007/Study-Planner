# Study Planner

## Overview

Study Planner is a Python desktop application for organizing study tasks
by subject. It's being developed in phases — this README documents
**Phase 1: Core Foundation** only.

## Current Features

- Subject management (add, view, edit, delete)
- Task management (add, view, edit, delete)
- Task priorities: `Low`, `Medium`, `High`
- Task deadlines (`YYYY-MM-DD`, validated)
- Task status: `Not Started`, `In Progress`, `Completed`
- Local persistence to a JSON file (auto-created on first run)
- Basic desktop GUI (Tkinter) with a subject list and a task table
- Basic dashboard summary: total / completed / pending task counts

### Deletion safety

Deleting a subject that still has tasks attached is **blocked**, not
cascaded — the app tells you how many tasks are attached and asks you to
delete or reassign them first. This guarantees a task is never silently
lost as a side effect of removing a subject.

### Data integrity

- Subject and task IDs are never reused, even after deletions.
- Saves are written to a temp file and atomically swapped in, so an
  interrupted save can't corrupt existing data.
- If the data file is ever unreadable (corrupted), the app backs it up
  next to the original, warns you, and starts with a fresh empty planner
  instead of crashing or silently discarding the bad file.

## Tech Stack

- Python 3 (standard library only)
- Tkinter / ttk for the GUI
- JSON for local storage

## Project Structure

```
study-planner/
├── main.py          # Application entry point
├── models.py         # Subject / Task data representations
├── data_manager.py   # Validation, CRUD, and JSON persistence
├── gui.py             # Tkinter interface (main window + dialogs)
├── data/              # Auto-created on first run; holds planner.json
├── README.md
├── requirements.txt
└── .gitignore
```

- **models.py** — plain data classes for `Subject` and `Task`, with
  `to_dict` / `from_dict` for JSON conversion.
- **data_manager.py** — owns all subject/task state, all validation, ID
  assignment, and reading/writing `data/planner.json`. The GUI never
  touches the file or the data dicts directly.
- **gui.py** — the Tkinter window, task table, and the Add/Edit dialogs
  for subjects and tasks. Contains no persistence or validation logic of
  its own; it calls into `DataManager` and shows the results.
- **main.py** — creates the Tk root window, wires up `DataManager` and
  `StudyPlannerApp`, and starts the event loop.

## Installation

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

macOS/Linux:

```bash
source .venv/bin/activate
```

Then:

```bash
pip install -r requirements.txt
```

> Study Planner has no external dependencies — it only uses the Python
> standard library. On some Linux distributions, Tkinter isn't installed
> by default; if `import tkinter` fails, install it separately, e.g.
> `sudo apt install python3-tk` on Debian/Ubuntu.

## Running

```bash
python main.py
```

On first run, `data/planner.json` is created automatically.

## Testing performed

- Add / edit / delete subjects, including duplicate-name rejection
  (case-insensitive).
- Add / edit / delete tasks, including multiple tasks under one subject
  and every priority/status/deadline combination.
- Validation: empty title, empty subject name, invalid/missing deadline,
  invalid priority, invalid status, invalid subject selection.
- Deleting a subject that still has tasks is blocked with a clear
  message; the tasks remain intact afterward.
- Persistence: data survives closing and reopening the application, and
  IDs are never reused after a delete.
- Empty data file and corrupted data file are both handled without
  crashing.
- GUI: selecting, editing, and deleting tasks/subjects from the
  interface; window resizing.

## Current Limitations

The following are **not** implemented yet — planned for later phases:

- Calendar view / scheduling
- Study session / Pomodoro time tracking
- Notifications and reminders
- Advanced analytics and progress charts
- Automatic scheduling or AI recommendations
- Cloud sync
- Login / authentication
- Mobile support

## What Phase 2 should build

- Advanced dashboard/analytics (progress over time, tasks-per-subject
  breakdowns, overdue-task highlighting).
- Sorting/filtering the task table (by subject, priority, status,
  deadline).
- Possibly a calendar or due-soon view, without yet introducing
  scheduling, notifications, or sync — those stay in scope for a later
  phase per the original roadmap.
- Keep `models.py` / `data_manager.py` / `gui.py` split as-is; Phase 2
  should extend it rather than restructure working Phase 1 code.
