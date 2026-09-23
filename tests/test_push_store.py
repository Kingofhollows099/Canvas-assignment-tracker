import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from push_store import PushStore, normalizeFullItem, parseIso


def isoIn(days, now):
    return (now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


class NormalizeTests(unittest.TestCase):
    def test_requires_id_and_due(self):
        self.assertIsNone(normalizeFullItem({"id": "a-1"}))
        self.assertIsNone(normalizeFullItem({"dueAt": "2026-09-24T00:00:00Z"}))
        self.assertIsNone(normalizeFullItem("not a dict"))

    def test_coerces_and_defaults(self):
        item = normalizeFullItem({"id": "assignment-1", "dueAt": "2026-09-24T00:00:00Z",
                                    "type": "bogus", "points": True, "title": 5})
        self.assertEqual(item["type"], "assignment")   # unknown type falls back
        self.assertIsNone(item["points"])              # bool is not a valid points value
        self.assertEqual(item["title"], "Untitled")    # non-string title falls back


class PushStoreTests(unittest.TestCase):
    now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)

    def setUp(self):
        self.tempDir = tempfile.TemporaryDirectory()
        self.storePath = Path(self.tempDir.name) / "store.json"
        self.store = PushStore(self.storePath, lookbackDays=14, horizonDays=90)

    def tearDown(self):
        self.tempDir.cleanup()

    def test_sync_filters_submitted_and_sorts(self):
        accepted, skipped = self.store.sync([
            {"id": "assignment-3", "dueAt": isoIn(3, self.now), "title": "Later"},
            {"id": "assignment-1", "dueAt": isoIn(1, self.now), "title": "Sooner"},
            {"id": "assignment-2", "dueAt": isoIn(2, self.now), "title": "Done", "submitted": True},
            {"id": "no-due"},  # skipped: no due date
        ], now=self.now)
        self.assertEqual((accepted, skipped), (3, 1))

        incomplete = self.store.getIncomplete(now=self.now)
        self.assertEqual([item["id"] for item in incomplete], ["assignment-1", "assignment-3"])
        self.assertNotIn("seenAt", incomplete[0])  # internal field isn't exposed

    def test_later_push_marks_submitted_and_removes_it(self):
        self.store.sync([{"id": "assignment-1", "dueAt": isoIn(1, self.now)}], now=self.now)
        self.assertEqual(len(self.store.getIncomplete(now=self.now)), 1)
        # Turning it in on Canvas produces a later push with submitted=True.
        self.store.sync([{"id": "assignment-1", "dueAt": isoIn(1, self.now), "submitted": True}], now=self.now)
        self.assertEqual(self.store.getIncomplete(now=self.now), [])

    def test_persists_across_instances(self):
        self.store.sync([{"id": "assignment-1", "dueAt": isoIn(1, self.now)}], now=self.now)
        reopened = PushStore(self.storePath, lookbackDays=14, horizonDays=90)
        self.assertEqual(len(reopened.getIncomplete(now=self.now)), 1)

    def test_prunes_long_past_items(self):
        self.store.sync([{"id": "old", "dueAt": isoIn(-30, self.now)},
                         {"id": "recent", "dueAt": isoIn(-1, self.now)}], now=self.now)
        self.assertNotIn("old", self.store.itemsById)
        self.assertIn("recent", self.store.itemsById)

    def test_window_excludes_far_future(self):
        self.store.sync([{"id": "far", "dueAt": isoIn(200, self.now)}], now=self.now)
        self.assertEqual(self.store.getIncomplete(now=self.now), [])

    def test_sync_rejects_non_list(self):
        with self.assertRaises(ValueError):
            self.store.sync({"not": "a list"})


class ParseIsoTests(unittest.TestCase):
    def test_handles_z_and_offset_and_junk(self):
        self.assertEqual(parseIso("2026-09-24T00:00:00Z").tzinfo, timezone.utc)
        self.assertIsNotNone(parseIso("2026-09-24T00:00:00+00:00"))
        self.assertIsNone(parseIso("not a date"))
        self.assertIsNone(parseIso(None))


if __name__ == "__main__":
    unittest.main()


class StatusUpdateAndCourseTests(unittest.TestCase):
    now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)

    def setUp(self):
        self.tempDir = tempfile.TemporaryDirectory()
        self.store = PushStore(Path(self.tempDir.name) / "s.json", lookbackDays=14, horizonDays=90)

    def tearDown(self):
        self.tempDir.cleanup()

    def test_status_only_update_flips_existing_without_due_date(self):
        # A grades-page push creates the item...
        self.store.sync([{"id": "assignment-1", "dueAt": isoIn(2, self.now),
                          "course": "MATH 241", "courseId": "10"}], now=self.now)
        self.assertEqual(len(self.store.getIncomplete(now=self.now)), 1)
        # ...then the assignment page (no due date read) flips it submitted.
        accepted, skipped = self.store.sync([{"id": "assignment-1", "submitted": True}], now=self.now)
        self.assertEqual((accepted, skipped), (1, 0))
        self.assertEqual(self.store.getIncomplete(now=self.now), [])

    def test_status_only_update_for_unknown_item_is_skipped(self):
        accepted, skipped = self.store.sync([{"id": "assignment-999", "submitted": True}], now=self.now)
        self.assertEqual((accepted, skipped), (0, 1))

    def test_course_name_backfilled_across_pushes(self):
        # Assignment page knows the course id but not (yet) the name.
        self.store.sync([{"id": "assignment-2", "dueAt": isoIn(1, self.now), "courseId": "10"}], now=self.now)
        # Grades page later supplies the name for course 10.
        self.store.sync([{"id": "assignment-3", "dueAt": isoIn(1, self.now),
                          "course": "PHYS 201", "courseId": "10"}], now=self.now)
        byId = {item["id"]: item for item in self.store.getIncomplete(now=self.now)}
        self.assertEqual(byId["assignment-2"]["course"], "PHYS 201")

    def test_course_id_is_preserved(self):
        self.store.sync([{"id": "assignment-2", "dueAt": isoIn(1, self.now),
                          "course": "CS 350", "courseId": "42"}], now=self.now)
        self.assertEqual(self.store.getIncomplete(now=self.now)[0]["courseId"], "42")
