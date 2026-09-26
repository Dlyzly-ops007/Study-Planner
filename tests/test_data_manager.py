"""Core data tests: CRUD, filtering, analytics consistency, load validation."""

import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_manager import DataManager, SubjectHasTasksError, ValidationError  # noqa: E402

TODAY = date(2026, 9, 26)  # a Saturday


def d(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


class DataManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "data", "planner.json")
        self.dm = DataManager(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self):
        self.math = self.dm.add_subject("Math")
        self.phys = self.dm.add_subject("Physics")
        self.t1 = self.dm.add_task("Homework 1", self.math.id, "High", d(-2), "Not Started")
        self.t2 = self.dm.add_task("Read ch. 3", self.math.id, "Low", d(0), "In Progress")
        self.t3 = self.dm.add_task("Lab report", self.phys.id, "Medium", d(1), "Completed")
        self.t4 = self.dm.add_task("Exam prep", self.phys.id, "High", d(5), "Not Started")
        self.dm.add_session(self.math.id, d(0), 60, self.t1.id, "morning")
        self.dm.add_session(self.phys.id, d(-1), 30)
        self.dm.add_session(self.math.id, d(-40), 45)


class TestCrud(DataManagerTestCase):
    def test_subject_crud_and_duplicate_names(self):
        s = self.dm.add_subject("  Chemistry ")
        self.assertEqual(s.name, "Chemistry")
        with self.assertRaises(ValidationError):
            self.dm.add_subject("chemistry")
        self.dm.edit_subject(s.id, "Chem", "desc")
        self.assertEqual(self.dm.get_subject(s.id).description, "desc")
        self.dm.delete_subject(s.id)
        self.assertIsNone(self.dm.get_subject(s.id))

    def test_subject_with_tasks_cannot_be_deleted(self):
        self.seed()
        with self.assertRaises(SubjectHasTasksError):
            self.dm.delete_subject(self.math.id)

    def test_task_validation(self):
        self.seed()
        for bad in [("", self.math.id, "High", d(1), "Not Started"),
                    ("x", 999, "High", d(1), "Not Started"),
                    ("x", self.math.id, "Urgent", d(1), "Not Started"),
                    ("x", self.math.id, "High", "2026-02-30", "Not Started"),
                    ("x", self.math.id, "High", d(1), "Done")]:
            with self.assertRaises(ValidationError):
                self.dm.add_task(*bad)

    def test_completion_date_follows_status(self):
        self.seed()
        self.assertTrue(self.t3.completion_date)
        self.dm.set_task_status(self.t3.id, "In Progress")
        self.assertEqual(self.dm.get_task(self.t3.id).completion_date, "")
        self.dm.set_task_status(self.t1.id, "Completed")
        self.assertEqual(self.dm.get_task(self.t1.id).completion_date, date.today().isoformat())

    def test_session_validation(self):
        self.seed()
        with self.assertRaises(ValidationError):
            self.dm.add_session(self.math.id, d(0), 0)
        with self.assertRaises(ValidationError):
            self.dm.add_session(self.math.id, d(0), "abc")
        with self.assertRaises(ValidationError):
            self.dm.add_session(self.phys.id, d(0), 10, task_id=self.t1.id)  # task is Math's
        with self.assertRaises(ValidationError) as ctx:
            self.dm.add_session(self.math.id, "nope", 10)
        self.assertIn("Date", str(ctx.exception))

    def test_edit_and_delete_session(self):
        self.seed()
        s = self.dm.get_sessions()[0]
        self.dm.edit_session(s.id, s.subject_id, s.date, 90, None, "edited")
        self.assertEqual(self.dm.get_session(s.id).duration_minutes, 90)
        self.dm.delete_session(s.id)
        self.assertIsNone(self.dm.get_session(s.id))

    def test_deleting_task_keeps_its_sessions(self):
        self.seed()
        self.dm.delete_task(self.t1.id)
        self.assertEqual(len(self.dm.sessions), 3)


class TestFiltering(DataManagerTestCase):
    def test_filters_and_clear(self):
        self.seed()
        f = self.dm.get_tasks_filtered
        self.assertEqual(len(f(today=TODAY)), 4)
        self.assertEqual([t.id for t in f(search="HOMEWORK", today=TODAY)], [self.t1.id])
        self.assertEqual(len(f(search="physics", today=TODAY)), 2)  # subject name searchable
        self.assertEqual(len(f(status="Completed", today=TODAY)), 1)
        self.assertEqual(len(f(priority="High", today=TODAY)), 2)
        self.assertEqual(len(f(subject_id=self.math.id, today=TODAY)), 2)
        self.assertEqual([t.id for t in f(deadline_filter="Overdue", today=TODAY)], [self.t1.id])
        self.assertEqual([t.id for t in f(deadline_filter="Today", today=TODAY)], [self.t2.id])
        self.assertEqual(len(f(exclude_completed=True, today=TODAY)), 3)

    def test_sorting(self):
        self.seed()
        by_priority = self.dm.get_tasks_filtered(sort_by="priority", today=TODAY)
        self.assertEqual(by_priority[0].priority, "High")
        self.assertEqual(by_priority[-1].priority, "Low")
        by_deadline = self.dm.get_tasks_filtered(today=TODAY)
        self.assertEqual([t.deadline for t in by_deadline], sorted(t.deadline for t in by_deadline))

    def test_deadline_categories(self):
        self.seed()
        cat = lambda t: self.dm.deadline_category(t, TODAY)  # noqa: E731
        self.assertEqual(cat(self.t1), "overdue")
        self.assertEqual(cat(self.t2), "today")
        self.assertEqual(cat(self.t3), "completed")
        self.assertEqual(cat(self.t4), "upcoming")


class TestAnalyticsConsistency(DataManagerTestCase):
    def test_totals_match_sessions(self):
        self.seed()
        study = self.dm.get_study_time_summary(TODAY)
        self.assertEqual(study["total"], sum(s.duration_minutes for s in self.dm.sessions.values()))
        self.assertEqual(study["today"], 60)
        self.assertEqual(sum(m for _, m in self.dm.get_study_minutes_by_subject(today=TODAY)), study["total"])
        per_subject = sum(self.dm.get_subject_summary(s.id, today=TODAY)["study_minutes"]
                          for s in self.dm.get_subjects())
        self.assertEqual(per_subject, study["total"])

    def test_dashboard_matches_task_summary(self):
        self.seed()
        dash = self.dm.get_dashboard_summary(TODAY)
        summary = self.dm.get_summary(TODAY)
        self.assertEqual(dash["task"], summary)
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["overdue"], 1)
        self.assertEqual(summary["completion_pct"], 25.0)
        self.assertEqual(dash["most_studied_subject"], "Math")

    def test_completion_pct_consistent_between_views(self):
        self.seed()
        for name, completed, total in self.dm.get_task_completion_by_subject():
            subject = next(s for s in self.dm.get_subjects() if s.name == name)
            pct = self.dm.get_subject_summary(subject.id, today=TODAY)["completion_pct"]
            self.assertAlmostEqual(pct, round(completed / total * 100, 1))

    def test_periods(self):
        self.seed()
        self.assertEqual(len(self.dm.get_sessions_filtered("This month", today=TODAY)), 2)
        self.assertEqual(len(self.dm.get_sessions_filtered("All time", today=TODAY)), 3)
        by_day = self.dm.get_study_minutes_by_day("This week", today=TODAY)
        self.assertEqual(len(by_day), 6)  # Monday..Saturday, zero-filled

    def test_empty_data(self):
        self.assertEqual(self.dm.get_summary(TODAY)["completion_pct"], 0.0)
        self.assertEqual(self.dm.get_study_minutes_by_day("All time", today=TODAY), [])
        self.assertIsNone(self.dm.get_dashboard_summary(TODAY)["most_studied_subject"])


class TestPersistenceAndValidation(DataManagerTestCase):
    def test_round_trip(self):
        self.seed()
        reopened = DataManager(self.path)
        self.assertEqual(reopened.to_payload(), self.dm.to_payload())
        self.assertIsNone(reopened.startup_warning)

    def _write(self, payload):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(payload if isinstance(payload, str) else json.dumps(payload))

    def test_corrupted_json_is_preserved(self):
        self._write("{not json")
        dm = DataManager(self.path)
        self.assertIn("corrupted", dm.startup_warning)
        backups = [n for n in os.listdir(os.path.dirname(self.path)) if "corrupted" in n]
        self.assertEqual(len(backups), 1)
        with open(os.path.join(os.path.dirname(self.path), backups[0]), encoding="utf-8") as f:
            self.assertEqual(f.read(), "{not json")

    def test_invalid_records_repaired_with_original_kept(self):
        self._write({
            "subjects": [{"id": 1, "name": "Math"}, {"name": "no id"}],
            "tasks": [
                {"id": 1, "title": "A", "subject_id": 1, "priority": "Urgent",
                 "status": "Done", "deadline": "31/12/2026"},
                {"id": 2, "title": "Orphan", "subject_id": 42, "deadline": "2026-10-01"},
                {"id": "x", "title": "bad id", "subject_id": 1},
            ],
            "study_sessions": [
                {"id": 1, "subject_id": 1, "date": "2026-09-01", "duration_minutes": -5},
                {"id": 2, "subject_id": 1, "date": "2026-09-01", "duration_minutes": 30},
            ],
            "next_task_id": 1,  # stale counter
        })
        dm = DataManager(self.path)
        self.assertIn("repaired", dm.startup_warning)
        task = dm.get_task(1)
        self.assertEqual((task.priority, task.status, task.deadline), ("Medium", "Not Started", ""))
        self.assertIsNotNone(dm.get_task(2))       # orphan kept, not dropped
        self.assertEqual(len(dm.tasks), 2)
        self.assertEqual(list(dm.sessions), [2])
        self.assertEqual(dm.add_task("New", 1, "Low", "2026-10-10", "Not Started").id, 3)
        self.assertTrue(any("before_repair" in n for n in os.listdir(os.path.dirname(self.path))))

    def test_wrong_top_level_shape_treated_as_corrupted(self):
        self._write([1, 2, 3])
        dm = DataManager(self.path)
        self.assertIn("corrupted", dm.startup_warning)
        self.assertEqual(dm.tasks, {})

    def test_old_phase1_file_loads(self):
        self._write({"subjects": [{"id": 1, "name": "Math"}],
                     "tasks": [{"id": 1, "title": "A", "subject_id": 1, "priority": "Low",
                                "deadline": "2026-10-01", "status": "Not Started"}]})
        dm = DataManager(self.path)
        self.assertIsNone(dm.startup_warning)
        self.assertEqual(len(dm.tasks), 1)


if __name__ == "__main__":
    unittest.main()
