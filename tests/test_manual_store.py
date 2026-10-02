import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from manual_store import ManualStore


class ManualStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempDir = tempfile.TemporaryDirectory()
        self.store = ManualStore(Path(self.tempDir.name) / "manual.json")

    def tearDown(self):
        self.tempDir.cleanup()

    def test_add_returns_normalized_item(self):
        item = self.store.add({"title": "  Read Ch. 4 ", "course": " Study Group ",
                               "dueAt": "2026-10-05T04:59:00Z", "points": 10})
        self.assertTrue(item["id"].startswith("manual-"))
        self.assertEqual(item["title"], "Read Ch. 4")     # trimmed
        self.assertEqual(item["course"], "Study Group")   # trimmed
        self.assertEqual(item["dueAt"], "2026-10-05T04:59:00Z")
        self.assertEqual(item["points"], 10)
        self.assertTrue(item["manual"])
        self.assertFalse(item["submitted"])
        self.assertEqual(item["courseId"], "manual")

    def test_title_and_due_required(self):
        with self.assertRaises(ValueError):
            self.store.add({"title": "   ", "dueAt": "2026-10-05T04:59:00Z"})
        with self.assertRaises(ValueError):
            self.store.add({"title": "x", "dueAt": "not a date"})

    def test_course_defaults_to_custom(self):
        item = self.store.add({"title": "x", "dueAt": "2026-10-05T04:59:00Z"})
        self.assertEqual(item["course"], "Custom")

    def test_delete(self):
        item = self.store.add({"title": "x", "dueAt": "2026-10-05T04:59:00Z"})
        self.assertTrue(self.store.delete(item["id"]))
        self.assertFalse(self.store.delete(item["id"]))
        self.assertEqual(self.store.getAll(), [])

    def test_persists_and_sorts(self):
        self.store.add({"title": "later", "dueAt": "2026-10-10T04:59:00Z"})
        self.store.add({"title": "sooner", "dueAt": "2026-10-01T04:59:00Z"})
        reopened = ManualStore(self.store.storePath)
        titles = [i["title"] for i in reopened.getAll()]
        self.assertEqual(titles, ["sooner", "later"])

    def test_rejects_non_dict(self):
        with self.assertRaises(ValueError):
            self.store.add("nope")


if __name__ == "__main__":
    unittest.main()
