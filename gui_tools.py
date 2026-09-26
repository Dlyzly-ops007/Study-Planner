"""
Phase 4 GUI pieces, kept out of gui.py so it doesn't keep growing:

- SettingsTab       -- small preferences form backed by settings.Settings
- ReminderController -- in-app reminder banner + a once-per-item popup
- FileActions       -- File menu: CSV/report export, backup, restore

Like the rest of the GUI, these only present things; data rules live in
data_manager.py / exporter.py / reminders.py / settings.py.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

import exporter
import reminders
from data_manager import StorageError
from models import PRIORITIES, STATUSES
from settings import REMINDER_WINDOW_CHOICES
from validation import PayloadError

if TYPE_CHECKING:
    from gui import StudyPlannerApp

# How often (ms) to re-check reminders while the app stays open, so a task
# that becomes due "today" after midnight is still announced.
REMINDER_CHECK_INTERVAL_MS = 30 * 60 * 1000


class SettingsTab(ttk.Frame):
    def __init__(self, parent: ttk.Notebook, app: "StudyPlannerApp"):
        super().__init__(parent)
        self.app = app
        self.settings = app.settings
        self._build()

    def _build(self) -> None:
        form = ttk.LabelFrame(self, text="Preferences")
        form.pack(anchor="nw", padx=12, pady=12)

        self.priority_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.duration_var = tk.StringVar()
        self.reminders_var = tk.BooleanVar()
        self.window_var = tk.StringVar()

        rows = [
            ("Default task priority:", ttk.Combobox(
                form, textvariable=self.priority_var, values=list(PRIORITIES), state="readonly", width=16)),
            ("Default task status:", ttk.Combobox(
                form, textvariable=self.status_var, values=list(STATUSES), state="readonly", width=16)),
            ("Default session length (minutes):", ttk.Spinbox(
                form, textvariable=self.duration_var, from_=5, to=600, increment=5, width=16)),
            ("Deadline reminders:", ttk.Checkbutton(
                form, variable=self.reminders_var, text="Show reminders for upcoming deadlines")),
            ("Remind me about tasks due within:", ttk.Combobox(
                form, textvariable=self.window_var, state="readonly", width=16,
                values=[f"{d} day{'s' if d > 1 else ''}" for d in REMINDER_WINDOW_CHOICES])),
        ]
        for r, (label, widget) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=r, column=0, sticky="w", padx=10, pady=5)
            widget.grid(row=r, column=1, sticky="w", padx=10, pady=5)

        btn_row = ttk.Frame(form)
        btn_row.grid(row=len(rows), column=0, columnspan=2, sticky="w", padx=10, pady=(8, 10))
        ttk.Button(btn_row, text="Save Settings", command=self._save).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Restore Defaults", command=self._reset).pack(side="left")

        ttk.Label(
            self, foreground="#555555", justify="left",
            text=(f"Your data is stored locally in:\n{self.app.data_dir}\n\n"
                  "Use the File menu to export CSV files, create a backup, or restore one."),
        ).pack(anchor="nw", padx=14)

    def refresh(self) -> None:
        s = self.settings
        self.priority_var.set(s.get("default_priority"))
        self.status_var.set(s.get("default_status"))
        self.duration_var.set(str(s.get("default_session_minutes")))
        self.reminders_var.set(s.get("reminders_enabled"))
        days = s.get("reminder_window_days")
        self.window_var.set(f"{days} day{'s' if days > 1 else ''}")

    def _save(self) -> None:
        try:
            minutes = int(self.duration_var.get().strip())
        except ValueError:
            messagebox.showerror("Cannot save settings", "Session length must be a whole number of minutes.")
            return
        try:
            self.settings.update(
                default_priority=self.priority_var.get(),
                default_status=self.status_var.get(),
                default_session_minutes=minutes,
                reminders_enabled=self.reminders_var.get(),
                reminder_window_days=int(self.window_var.get().split()[0]),
            )
        except (ValueError, StorageError) as exc:
            messagebox.showerror("Cannot save settings", str(exc))
            return
        self.app.reminders.refresh_banner()
        messagebox.showinfo("Settings", "Settings saved.")

    def _reset(self) -> None:
        if not messagebox.askyesno("Restore Defaults", "Reset all settings to their default values?"):
            return
        try:
            self.settings.reset()
        except StorageError as exc:
            messagebox.showerror("Cannot save settings", str(exc))
            return
        self.refresh()
        self.app.reminders.refresh_banner()


class ReminderController:
    """
    Owns the reminder banner under the app header. The banner is always
    current; the popup only ever lists items not yet shown this run
    (reminders.ReminderTracker), so periodic checks never spam.
    """

    def __init__(self, app: "StudyPlannerApp", banner_parent: tk.Widget):
        self.app = app
        self.tracker = reminders.ReminderTracker()
        self.banner_var = tk.StringVar()
        self.banner = ttk.Label(banner_parent, textvariable=self.banner_var, cursor="hand2",
                                foreground="#8a4b00")
        self.banner.pack(pady=(0, 4))
        self.banner.bind("<Button-1>", lambda _e: self.app.show_tab(self.app.upcoming_tab))

    def _groups(self):
        s = self.app.settings
        return reminders.get_reminders(self.app.dm, s.get("reminder_window_days"))

    def refresh_banner(self) -> None:
        if not self.app.settings.get("reminders_enabled"):
            self.banner_var.set("")
            return
        groups = self._groups()
        text = reminders.summary_line(groups)
        if any(groups.values()):
            text = f"Reminders -- {text}   (click to view)"
        self.banner_var.set(text)

    def check_and_notify(self) -> None:
        """Pop up anything new, then schedule the next check."""
        try:
            if self.app.settings.get("reminders_enabled"):
                fresh = self.tracker.new_items(self._groups())
                if any(fresh.values()):
                    messagebox.showinfo("Deadline Reminders", reminders.format_details(self.app.dm, fresh))
            self.refresh_banner()
        finally:
            self.app.root.after(REMINDER_CHECK_INTERVAL_MS, self.check_and_notify)


class FileActions:
    """Handlers for the File menu."""

    def __init__(self, app: "StudyPlannerApp"):
        self.app = app
        self.dm = app.dm

    def build_menu(self, menubar: tk.Menu) -> None:
        menu = tk.Menu(menubar, tearoff=0)
        menu.add_command(label="Export Tasks (CSV)...", command=self.export_tasks)
        menu.add_command(label="Export Study Sessions (CSV)...", command=self.export_sessions)
        menu.add_command(label="Export Summary Report (TXT)...", command=self.export_report)
        menu.add_separator()
        menu.add_command(label="Create Backup...", command=self.create_backup)
        menu.add_command(label="Restore from Backup...", command=self.restore_backup)
        menu.add_separator()
        menu.add_command(label="Exit", command=self.app.root.destroy)
        menubar.add_cascade(label="File", menu=menu)

    def _ask_save(self, title: str, default_name: str, ext: str, kind: str):
        return filedialog.asksaveasfilename(
            parent=self.app.root, title=title, initialfile=default_name, defaultextension=ext,
            filetypes=[(kind, f"*{ext}"), ("All files", "*.*")],
        )

    def _export(self, title: str, default_name: str, ext: str, kind: str, write) -> None:
        path = self._ask_save(title, default_name, ext, kind)
        if not path:
            return
        try:
            detail = write(path)
        except OSError as exc:
            messagebox.showerror("Export failed", f"Could not write the file:\n{exc}")
            return
        messagebox.showinfo("Export complete", f"{detail}\n\nSaved to:\n{path}")

    def export_tasks(self) -> None:
        self._export("Export Tasks", "tasks.csv", ".csv", "CSV files",
                     lambda p: f"Exported {exporter.export_tasks_csv(self.dm, p)} task(s).")

    def export_sessions(self) -> None:
        self._export("Export Study Sessions", "study_sessions.csv", ".csv", "CSV files",
                     lambda p: f"Exported {exporter.export_sessions_csv(self.dm, p)} study session(s).")

    def export_report(self) -> None:
        def write(p):
            exporter.export_summary_report(self.dm, p)
            return "Summary report exported."
        self._export("Export Summary Report", "study_summary.txt", ".txt", "Text files", write)

    def create_backup(self) -> None:
        def write(p):
            exporter.create_backup(self.dm, p)
            return "Backup created."
        self._export("Create Backup", exporter.default_backup_name(), ".json", "Backup files", write)

    def restore_backup(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.app.root, title="Restore from Backup",
            filetypes=[("Backup files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            payload, parsed = exporter.load_backup(path)
        except OSError as exc:
            messagebox.showerror("Restore failed", f"Could not read the backup:\n{exc}")
            return
        except PayloadError as exc:
            messagebox.showerror("Invalid backup", f"{exc}\n\nYour current data was not changed.")
            return

        if not messagebox.askyesno(
            "Restore from Backup",
            f"This backup contains {len(parsed.subjects)} subject(s), {len(parsed.tasks)} task(s) "
            f"and {len(parsed.sessions)} study session(s).\n\n"
            "Restoring REPLACES all current planner data. A safety copy of your current "
            "data will be saved first.\n\nContinue?",
            icon="warning",
        ):
            return
        safety_dir = os.path.join(self.app.data_dir, "backups")
        try:
            safety_path = exporter.restore_backup(self.dm, payload, safety_dir)
        except (OSError, StorageError, PayloadError) as exc:
            messagebox.showerror("Restore failed", f"{exc}\n\nYour current data was not changed.")
            return
        self.app.notify_data_changed()
        messagebox.showinfo(
            "Restore complete",
            f"Backup restored.\n\nYour previous data was saved to:\n{safety_path}",
        )
