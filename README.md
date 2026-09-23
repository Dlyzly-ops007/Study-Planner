# Study Planner

## Overview

Study Planner is a Python desktop application for organizing study tasks
by subject. It's being developed in phases — this README documents
**Phase 1 (Core Foundation)** and **Phase 2 (Planning & Organization)**.

## Current Functionality

### From Phase 1
- Subject management (add, view, edit, delete)
- Task management (add, view, edit, delete)
- Task priorities: `Low`, `Medium`, `High`
- Task deadlines (`YYYY-MM-DD`, validated)
- Task status: `Not Started`, `In Progress`, `Completed`
- Local JSON persistence, auto-created on first run
- Basic desktop GUI (Tkinter)

### New in Phase 2
- **Search** — matches task title, description, or subject name;
  case-insensitive, partial-match, never modifies stored data.
- **Filters** — Status, Priority, Subject, and Deadline (`Today` /
  `This week` / `Overdue` / `Upcoming`), all combinable, plus a
  "Clear Filters" button. A "Showing X of Y tasks" counter (or a
  friendly "No tasks match the current filters." message) always
  reflects the active filters.
- **Sorting** — click any task table column header to sort by that
  column; click again to reverse. Sorting only changes what's
  displayed, never the stored task order.
- **Deadline awareness** — overdue / due-today / due-tomorrow /
  upcoming are computed live from the current date, independent of a
  task's status (e.g. a task can be "Not Started" *and* "Overdue" at
  the same time). Overdue and due-soon rows are color-highlighted in
  the task table.
- **Upcoming tab** — a dedicated "Overdue" list and a "Due in the next
  7 days" list, both sorted chronologically.
- **Calendar tab** — a simple month grid (built with Tkinter + the
  standard `calendar` module, no extra dependency); days with tasks
  are marked, today is bracketed, and selecting a day shows that
  day's tasks below.
- **Quick status change** — right-click a task in the table to jump
  straight to Not Started / In Progress / Completed without opening
  the full edit form.
- **Completion dates** — marking a task Completed records today's
  date; moving it back off Completed clears that date. Editing an
  already-completed task does not overwrite its original completion
  date.
- **Expanded dashboard** — Total, Completed, In Progress, Not
  Started, Overdue, and Due Today counts, always current.
- **Subjects tab** — each subject now shows its total/completed/
  pending task counts.

## Planned Future Functionality (not yet implemented)

- Study session / Pomodoro time tracking
- Notifications and reminders
- Advanced analytics and progress charts
- Automatic scheduling or AI recommendations
- Cloud sync
- Login / authentication
- Mobile support

## Tech Stack

- Python 3 (standard library only — Tkinter/ttk for the GUI, `json`
  and `calendar` from the standard library)

## Project Structure

```
study-planner/
├── main.py          # Application entry point
├── models.py         # Subject / Task data representations
├── data_manager.py   # Validation, CRUD, search/filter/sort, JSON persistence
├── gui.py             # Tkinter interface: Tasks / Upcoming / Calendar / Subjects tabs
├── data/               # Auto-created on first run; holds planner.json
├── README.md
├── requirements.txt
└── .gitignore
```

- **models.py** — plain data classes for `Subject` and `Task`
  (`Task` now also carries `completion_date`), with `to_dict` /
  `from_dict` for JSON conversion. `from_dict` uses `.get()` with
  defaults, so **Phase 1 data files load unchanged** — a task saved
  before Phase 2 simply has an empty `completion_date` until it's
  next marked Completed.
- **data_manager.py** — still owns all state, validation, ID
  assignment, and file I/O. Phase 2 adds: `get_tasks_filtered()`
  (search + filters + sorting in one place), `deadline_category()`
  (overdue/today/tomorrow/upcoming/later/completed classification),
  `set_task_status()` (quick status changes with completion-date
  handling shared with `edit_task()`), `get_tasks_by_date()` /
  `get_task_days_in_month()` (calendar support), and
  `get_subject_summary()` (per-subject counts). The GUI still never
  touches the file or the task/subject dicts directly.
- **gui.py** — reorganized into a `ttk.Notebook` with four tabs
  (`TasksTab`, `UpcomingTab`, `CalendarTab`, `SubjectsTab`) plus the
  `SubjectDialog` / `TaskDialog` popups. No tab computes filtering,
  sorting, or date math itself — it all comes from `DataManager`.
- **main.py** — unchanged: creates the Tk root window, wires up
  `DataManager` and `StudyPlannerApp`, starts the event loop.

### Deletion safety (unchanged from Phase 1)

Deleting a subject that still has tasks attached is **blocked**, not
cascaded — you're told how many tasks are attached and asked to
delete or reassign them first, so a task is never silently lost.

### Data integrity (unchanged from Phase 1)

- IDs are never reused, even after deletions.
- Saves are written to a temp file and atomically swapped in.
- A corrupted data file gets backed up and the app starts fresh
  instead of crashing or silently discarding it.

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

**Search:** title, description, and subject-name matches; partial and
case-insensitive matching; no-match case returns an empty result (and
a friendly message in the UI), not an error.

**Filters:** status, priority, subject, and each deadline bucket
individually and combined together; "Clear Filters" resets all of
them.

**Sorting:** deadline, priority, status, subject, and title, both
ascending and descending via repeated header clicks; confirmed the
stored task order in the data file is unaffected by display sorting.

**Deadlines:** overdue, due-today, due-tomorrow, this-week, and
later-than-a-week tasks all classified correctly; confirmed a
completed task is never flagged overdue/due-soon.

**Status workflow:** Not Started → In Progress → Completed → back to
an incomplete status via the quick right-click menu and via the full
edit form; confirmed a completion date is recorded on completion,
cleared when un-completed, and *not* overwritten when re-editing an
already-completed task.

**Calendar:** navigating between months, selecting a date with tasks,
a date without tasks, and a date with multiple tasks.

**Persistence & backward compatibility:** closing and reopening the
app preserves all Phase 2 data (including completion dates); loading
a Phase-1-style JSON file (no `completion_date` field at all) loads
without errors and defaults that field to empty.

**Edge cases:** an orphaned task (subject deleted outside the app, e.g.
by hand-editing the JSON) still filters/sorts without crashing and
shows as "(deleted)" in the subject column.

All of the above were run as real Python test scripts against the
actual `DataManager` and `gui.py` widgets (the latter driven headlessly
via Xvfb), not just read over.

## What Phase 3 should build

- Advanced analytics/charts (progress over time, completion rate per
  subject, workload distribution).
- Study session / time tracking, still without notifications, AI, or
  sync — those stay out of scope until a later phase per the original
  roadmap.
- Keep extending `data_manager.py` / `gui.py` rather than restructuring
  the Phase 1/2 module split, which has held up well across two
  phases now.
