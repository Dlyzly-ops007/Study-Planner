"""Entry point for the Study Planner application."""

import os
import tkinter as tk

from data_manager import DataManager
from gui import StudyPlannerApp

# Path is relative to this file, not an absolute personal path, so the
# project works the same on any machine/OS.
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "planner.json")


def main() -> None:
    root = tk.Tk()
    data_manager = DataManager(DATA_FILE)
    StudyPlannerApp(root, data_manager)
    root.mainloop()


if __name__ == "__main__":
    main()
