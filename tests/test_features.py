"""Phase 4 feature tests: reminders, settings, export, backup/restore."""

import csv
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import exporter  # noqa: E402
import reminders  # noqa: E402
from data_manager import DataManager  # noqa: E402
from settings import DEFAULTS, Settings  # noqa: E402
from test_data_manager import TODAY, DataManagerTestCase, d  # noqa: E402
from validation import PayloadError  # noqa: E402


class TestReminders(DataManagerTestCase):
    def test_grouping_and_window(self):
        self.seed()
        extra = self.dm.add_task("Tomorrow thing", self.math.id, "Low", d(1), "Not Started")
        groups = reminders.get_reminders(self.dm, window_days=3, today=TODAY)
        self.assertEqual([t.id for t in groups["overdue"]], [self.t1.id])
        self.assertEqual([t.id for t in groups["today"]], [self.t2.id])
        self.assertEqual([t.id for t in groups["tomorrow"]], [extra.id])  # completed t3 excluded
        self.assertEqual(groups["soon"], [])                              # t4 is 5 days out
        wide = reminders.get_reminders(self.dm, window_days=7, today=TODAY)
        self.assertEqual([t.id for t in wide["soon"]], [self.t4.id])

    def test_no_repeat_notifications(self):
        self.seed()
        tracker = reminders.ReminderTracker()
        groups = reminders.get_reminders(self.dm, 7, TODAY)
        first = tracker.new_items(groups)
        self.assertTrue(any(first.values()))
        self.assertFalse(any(tracker.new_items(groups).values()))
        # a task newly becoming overdue IS announced
        self.dm.edit_task(self.t2.id, "Read ch. 3", self.math.id, "Low", d(-1), "In Progress")
        fresh = tracker.new_items(reminders.get_reminders(self.dm, 7, TODAY))
        self.assertEqual([t.id for t in fresh["overdue"]], [self.t2.id])

    def test_empty_text(self):
        groups = reminders.get_reminders(self.dm, 3, TODAY)
        self.assertEqual(reminders.summary_line(groups), "No upcoming deadlines.")


class TestSettings(DataManagerTestCase):
    def test_defaults_persist_and_invalid_values(self):
        path = os.path.join(self.tmp.name, "data", "settings.json")
        s = Settings(path)
        self.assertEqual(s.values, DEFAULTS)
        s.update(default_priority="High", reminder_window_days=7)
        self.assertEqual(Settings(path).get("default_priority"), "High")
        with self.assertRaises(ValueError):
            s.update(default_session_minutes=0)
        with self.assertRaises(ValueError):
            s.update(reminders_enabled="yes")
        self.assertEqual(s.get("default_session_minutes"), DEFAULTS["default_session_minutes"])

    def test_bad_file_falls_back_to_defaults(self):
        path = os.path.join(self.tmp.name, "data", "settings.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"default_priority": "Urgent", "reminder_window_days": 7')  # truncated JSON
        self.assertEqual(Settings(path).values, DEFAULTS)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"default_priority": "Urgent", "reminder_window_days": 7}, f)
        s = Settings(path)
        self.assertEqual(s.get("default_priority"), "Medium")
        self.assertEqual(s.get("reminder_window_days"), 7)


class TestExport(DataManagerTestCase):
    def _read_csv(self, path):
        with open(path, newline="", encoding="utf-8-sig") as f:
            return list(csv.reader(f))

    def test_tasks_csv(self):
        self.seed()
        path = os.path.join(self.tmp.name, "tasks.csv")
        self.assertEqual(exporter.export_tasks_csv(self.dm, path), 4)
        rows = self._read_csv(path)
        self.assertEqual(rows[0][:8], ["ID", "Title", "Subject", "Priority", "Deadline", "Status",
                                       "Created", "Completed"])
        lab = next(r for r in rows if r[1] == "Lab report")
        self.assertEqual(lab[2], "Physics")
        self.assertTrue(lab[7])

    def test_sessions_csv_with_deleted_task(self):
        self.seed()
        self.dm.delete_task(self.t1.id)
        path = os.path.join(self.tmp.name, "sessions.csv")
        self.assertEqual(exporter.export_sessions_csv(self.dm, path), 3)
        rows = self._read_csv(path)
        self.assertEqual(rows[0], ["ID", "Date", "Subject", "Task", "Duration (minutes)", "Notes"])
        self.assertIn("(deleted task)", [r[3] for r in rows])

    def test_export_does_not_touch_data_file(self):
        self.seed()
        with open(self.path, encoding="utf-8") as f:
            before = f.read()
        exporter.export_tasks_csv(self.dm, os.path.join(self.tmp.name, "t.csv"))
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)

    def test_summary_report(self):
        self.seed()
        text = exporter.build_summary_report(self.dm, TODAY)
        self.assertIn("Completed:     1 (25.0%)", text)
        self.assertIn("Overdue:       1", text)
        self.assertIn("Total:         2h 15m", text)
        self.assertIn("Most studied:  Math", text)
        self.assertIn("No study sessions recorded.", exporter.build_summary_report(DataManager(
            os.path.join(self.tmp.name, "empty", "p.json")), TODAY))


class TestBackupRestore(DataManagerTestCase):
    def test_backup_and_restore_round_trip(self):
        self.seed()
        backup = os.path.join(self.tmp.name, "backup.json")
        exporter.create_backup(self.dm, backup)
        snapshot = self.dm.to_payload()

        self.dm.delete_session(1)
        self.dm.add_subject("Temporary")
        payload, parsed = exporter.load_backup(backup)
        self.assertEqual(len(parsed.tasks), 4)
        safety = exporter.restore_backup(self.dm, payload, os.path.join(self.tmp.name, "safety"))

        self.assertEqual(self.dm.to_payload(), snapshot)
        self.assertEqual(DataManager(self.path).to_payload(), snapshot)  # persisted
        with open(safety, encoding="utf-8") as f:
            self.assertIn("Temporary", f.read())  # pre-restore data kept

    def test_invalid_backups_rejected_without_changes(self):
        self.seed()
        before = self.dm.to_payload()
        cases = {
            "notjson.json": "{oops",
            "list.json": "[]",
            "other.json": json.dumps({"hello": "world"}),
            "badrecords.json": json.dumps({"subjects": [{"id": 1, "name": "M"}],
                                           "tasks": [{"id": 1, "title": "t", "subject_id": 1,
                                                      "priority": "Urgent"}]}),
        }
        for name, content in cases.items():
            path = os.path.join(self.tmp.name, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            with self.subTest(name), self.assertRaises(PayloadError):
                exporter.load_backup(path)
        with self.assertRaises(PayloadError):
            self.dm.replace_data({"subjects": [], "tasks": [{"id": 1}]})
        self.assertEqual(self.dm.to_payload(), before)


if __name__ == "__main__":
    unittest.main()
