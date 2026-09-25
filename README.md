# Study Planner

## Overview

Study Planner is a Python desktop application for organizing study tasks
and tracking study time by subject. It's being developed in phases —
this README documents **Phase 1 (Core Foundation)**, **Phase 2
(Planning & Organization)**, and **Phase 3 (Study Tracking &
Analytics)**.

## Current Functionality

### From Phase 1
- Subject management (add, view, edit, delete)
- Task management (add, view, edit, delete)
- Task priorities: `Low`, `Medium`, `High`
- Task deadlines (`YYYY-MM-DD`, validated)
- Task status: `Not Started`, `In Progress`, `Completed`
- Local JSON persistence, auto-created on first run

### From Phase 2
- Search (task title / description / subject name), combinable filters
  (status, priority, subject, deadline), and clickable-header sorting
- Deadline awareness (overdue / today / tomorrow / upcoming), shown as
  color-highlighted rows, independent of a task's status
- Upcoming tab (Overdue + Due in the next 7 days)
- Calendar tab — simple month grid, click a day to see its tasks
- Quick status change via right-click, without opening the full edit form
- Completion dates, recorded automatically when a task is marked Completed

### New in Phase 3
- **Study sessions** — log study time against a subject and, optionally,
  a specific task, with a date, a duration (minutes), and notes.
- **Study history** — a dedicated Study Sessions tab listing every
  logged session, filterable by period and subject, with edit/delete.
- **Study-time tracking** — running totals for Total / Today / This
  week / This month, shown in a human-friendly format (`1h 30m`, not
  `1.5h`).
- **Subject progress** — for every subject: total/completed/pending/
  overdue tasks, completion percentage, and total study time, all
  computed live from existing task/session data (nothing is stored as a
  separate "progress" value).
- **Task study time** — editing a task shows how much study time has
  been logged against it. Logging time never changes a task's status —
  completion and study time stay independent, so you can study for a
  task without the app assuming it's done.
- **Analytics tab** — a dashboard (task summary, study-time summary,
  most-studied subject, highest-completion subject) plus:
  - *Study Time by Subject* (bar chart)
  - *Study Time Over Time* (line chart)
  - *Task Completion by Subject* (bar chart, always current — task
    completion isn't a historical event the way logged time is, so this
    chart isn't affected by the period selector)
  - a Subject Statistics table
  - a productivity summary line (sessions logged, average session
    length, tasks completed, most-studied subject) for the selected period
- **Time period filtering** — Today / This week / This month / All time,
  applied consistently everywhere a period applies (study history,
  dashboard, charts, subject stats, productivity summary).
- Every chart and stats view handles **no data** gracefully (a "No study
  sessions recorded for this period." message instead of a broken or
  empty chart).

> **Note on "This week":** the task Deadline filter's "This week" (from
> Phase 2) looks *forward* — the next 7 days from today, since deadlines
> are upcoming events. The study-time "This week" period looks *backward*
> to the start of the current calendar week (Monday) through today, since
> logged time is a historical record. Both are documented here to avoid
> confusion; each matches how that kind of data is normally read.

## Planned Future Functionality (not yet implemented)

- Notifications and reminders
- Automatic scheduling
- AI recommendations
- Cloud synchronization
- Mobile support
- Advanced settings
- Complex recurring schedules
- Authentication
- Full calendar event management

## Tech Stack

- Python 3
- Tkinter / ttk for the GUI
- `json` and `calendar` from the standard library
- **matplotlib** — the one external dependency, used only for the three
  Analytics charts (embedded via `FigureCanvasTkAgg`, not opened in
  separate windows)

## Project Structure

```
study-planner/
├── main.py          # Application entry point
├── models.py         # Subject / Task / StudySession data representations
├── data_manager.py   # Validation, CRUD, search/filter/sort, stats, JSON persistence
├── gui.py             # Tkinter interface: 6 tabs + dialogs
├── data/               # Auto-created on first run; holds planner.json
├── README.md
├── requirements.txt
└── .gitignore
```

- **models.py** — `Subject`, `Task` (with `completion_date`), and the
  new `StudySession` (subject, optional task, date, duration in
  minutes, notes). `StudySession` records a manually-entered duration
  rather than start/end clock times — simpler to validate ("must be
  positive") and just as reliable for the totals/charts this app needs.
  `from_dict()` methods use `.get()` with defaults throughout, so
  **Phase 1 and Phase 2 data files load unchanged** — a file with no
  `study_sessions` key at all is simply treated as zero sessions.
- **data_manager.py** — still the single owner of all state, validation,
  ID assignment, and file I/O; the GUI never touches the file or the
  subject/task/session dicts directly. Phase 3 adds session CRUD
  (`add_session` / `edit_session` / `delete_session`), reusable
  date-period filtering (`get_sessions_filtered`, shared by the history
  view, dashboard, and analytics), duration formatting
  (`format_duration`), and a set of calculation functions used nowhere
  else but here: `get_study_time_summary`, `get_subject_summary`
  (extended with completion % / overdue / study time),
  `get_productivity_summary`, `get_dashboard_summary`, and three
  chart-data helpers (`get_study_minutes_by_subject`,
  `get_study_minutes_by_day`, `get_task_completion_by_subject`) — no
  chart or stats view computes its own numbers.
- **gui.py** — a `ttk.Notebook` with six tabs: `TasksTab`,
  `UpcomingTab`, `CalendarTab`, `StudySessionsTab` (new),
  `AnalyticsTab` (new), and `SubjectsTab`, plus `SubjectDialog`,
  `TaskDialog`, and the new `SessionDialog`. No tab computes filtering,
  sorting, date math, or statistics itself — it all comes from
  `DataManager`; `gui.py` only renders it (including the matplotlib
  figures, which are built once per tab and redrawn via `ax.clear()`
  rather than rebuilt from scratch on every refresh).
- **main.py** — unchanged.

### Deletion safety (extended in Phase 3)

Deleting a subject is blocked if it still has **tasks or study
sessions** attached — you're told what's attached and asked to clean it
up first, so nothing is ever silently lost. Deleting a *task*, by
contrast, is **not** blocked by its study sessions: sessions are a
historical record of time spent, and a task is often deleted well after
you've finished studying for it. A session that references a deleted
task keeps its stored `task_id` and simply displays as
`(deleted task)` in the history — the same pattern already used for a
deleted subject elsewhere in the app.

### Data integrity (unchanged principles, extended in Phase 3)

- IDs (subjects, tasks, *and now sessions*) are never reused, even
  after deletions.
- Saves are written to a temp file and atomically swapped in.
- A corrupted data file gets backed up and the app starts fresh instead
  of crashing or silently discarding it.
- All statistics are computed live from `self.tasks` / `self.sessions`
  on every call — there's no cached or separately-stored total that
  could drift out of sync after an edit or delete.

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

> On some Linux distributions, Tkinter isn't installed by default; if
> `import tkinter` fails, install it separately, e.g.
> `sudo apt install python3-tk` on Debian/Ubuntu.

## Running

```bash
python main.py
```

On first run, `data/planner.json` is created automatically.

## Testing performed

**Study sessions:** add, edit, delete; associated with a subject alone
and with a subject + task; rejected when the task doesn't belong to the
selected subject; zero/negative/non-numeric duration rejected; invalid
date rejected; empty notes allowed.

**Calculations:** total / daily / weekly / monthly study time; per-task
and per-subject study time; subject completion percentage; all cross-
checked against hand-computed expected values, including a
multi-session, multi-subject scenario.

**Analytics:** all three charts and the stats table tested with no
data, one subject, multiple subjects, and multiple dates across every
period option (Today / This week / This month / All time) — confirmed
no crashes and correct "no data" messaging when a period has nothing to
show.

**Interactions:** completing a task and separately logging study time
against it, confirming subject statistics update correctly after each;
editing and deleting sessions and confirming totals recalculate
immediately; deleting a task and confirming its sessions survive and
display as `(deleted task)`; deleting a subject with zero tasks but an
attached session, confirming deletion is still correctly blocked.

**Persistence & backward compatibility:** closing and reopening the app
preserves all sessions and their IDs; loading a Phase 2-style JSON file
with no `study_sessions` key at all loads cleanly as zero sessions, and
a new session can be logged into it immediately afterward.

All of the above were run as real Python test scripts against the
actual `DataManager` and `gui.py` widgets (the GUI tests driven
headlessly via Xvfb, including constructing the real matplotlib
canvases), not just read over. A full scripted run of the spec's
end-to-end workflow — subjects → tasks → sessions → history → subject
progress → analytics → period changes → edit/delete → restart → verify
persistence — was also executed successfully.

## What Phase 4 should build

- Notifications/reminders, automatic scheduling, and any "smart"
  suggestions, kept clearly separate from the reliable, deterministic
  calculations Phase 3 established.
- Continue extending `data_manager.py` / `gui.py` rather than
  restructuring — the module split has now held up cleanly across three
  phases, including a new external dependency (matplotlib) and a third
  data entity (`StudySession`) without needing to touch the overall
  architecture.
