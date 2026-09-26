"""
User preferences, stored in their own small JSON file next to the planner
data so a bad settings file can never affect tasks/sessions (and vice versa).

Every value is validated on load; anything missing or invalid silently
falls back to its default -- settings are low-stakes, unlike planner data.
"""

from __future__ import annotations

import json
import os

from data_manager import StorageError, write_json_atomic
from models import PRIORITIES, STATUSES

REMINDER_WINDOW_CHOICES = (1, 2, 3, 5, 7, 14)

DEFAULTS = {
    "default_priority": "Medium",
    "default_status": "Not Started",
    "default_session_minutes": 45,
    "reminders_enabled": True,
    "reminder_window_days": 3,
}


def _is_valid(key: str, value) -> bool:
    if key == "default_priority":
        return value in PRIORITIES
    if key == "default_status":
        return value in STATUSES
    if key == "default_session_minutes":
        return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 24 * 60
    if key == "reminders_enabled":
        return isinstance(value, bool)
    if key == "reminder_window_days":
        return value in REMINDER_WINDOW_CHOICES
    return False


class Settings:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.values = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                stored = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            # Unreadable settings: fall back to defaults; the file is simply
            # rewritten the next time the user saves settings.
            return
        if isinstance(stored, dict):
            for key in DEFAULTS:
                if key in stored and _is_valid(key, stored[key]):
                    self.values[key] = stored[key]

    def get(self, key: str):
        return self.values[key]

    def update(self, **changes) -> None:
        """Validate every change first, then persist. Raises ValueError
        (user-facing message) for invalid values, StorageError on write failure."""
        for key, value in changes.items():
            if key not in DEFAULTS:
                raise ValueError(f"Unknown setting: {key}")
            if not _is_valid(key, value):
                raise ValueError(f"Invalid value for {key.replace('_', ' ')}: {value!r}")
        previous = dict(self.values)
        self.values.update(changes)
        try:
            write_json_atomic(self.filepath, self.values)
        except OSError as exc:
            self.values = previous
            raise StorageError(f"Could not save settings:\n{exc}") from exc

    def reset(self) -> None:
        self.update(**DEFAULTS)


def settings_path_for(data_file: str) -> str:
    return os.path.join(os.path.dirname(data_file), "settings.json")
