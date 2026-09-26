"""Entry point for the Study Planner application."""

import os
import tkinter as tk
from tkinter import messagebox

from data_manager import DataManager, StorageError
from gui import StudyPlannerApp
from settings import Settings, settings_path_for

# Path is relative to this file, not an absolute personal path, so the
# project works the same on any machine/OS.
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "planner.json")


def main() -> None:
    root = tk.Tk()
    try:
        data_manager = DataManager(DATA_FILE)
    except StorageError as exc:
        # Unreadable (not corrupted) file, e.g. a permissions problem:
        # don't start with empty data that could later overwrite it.
        root.withdraw()
        messagebox.showerror("Study Planner", f"{exc}\n\nThe application will now close.")
        root.destroy()
        return
    settings = Settings(settings_path_for(DATA_FILE))
    StudyPlannerApp(root, data_manager, settings)
    root.mainloop()


if __name__ == "__main__":
    main()
