"""Manually-added assignments, created by the user in the web UI.

These are independent of Canvas: you give a title, a course name of your choosing
(a "custom course"), a due date/time and optional points. They persist to their own
JSON file and are merged into the calendar and to-do views alongside synced work.

Only the signed-in user can add or remove them (the server gates the endpoints on a
session), so unlike the Canvas push store there is no untrusted-input concern beyond
basic validation and bounding.
"""

import secrets
import threading
from datetime import datetime, timezone

from push_store import coerceString, coercePoints, parseIso

maxManualItems = 1000


def toIso(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ManualStore:
    """User-created assignments, persisted to a JSON file."""

    def __init__(self, storePath):
        self.storePath = storePath
        self.storeLock = threading.Lock()
        self.itemsById = {}
        self._load()

    def _load(self):
        try:
            import json
            saved = json.loads(self.storePath.read_text(encoding="utf-8"))
            self.itemsById = {item["id"]: item for item in saved.get("assignments", [])
                              if isinstance(item, dict) and "id" in item}
        except (FileNotFoundError, ValueError):
            self.itemsById = {}

    def _save(self):
        import json
        payload = {"assignments": list(self.itemsById.values())}
        tempPath = self.storePath.with_suffix(self.storePath.suffix + ".tmp")
        tempPath.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tempPath.replace(self.storePath)

    def add(self, rawItem):
        """Validate and store a new manual assignment; return the stored item."""
        if not isinstance(rawItem, dict):
            raise ValueError("Expected an assignment object.")
        title = coerceString(rawItem.get("title")).strip()
        if not title:
            raise ValueError("A title is required.")
        dueMoment = parseIso(coerceString(rawItem.get("dueAt")))
        if dueMoment is None:
            raise ValueError("A valid due date and time are required.")
        course = coerceString(rawItem.get("course")).strip() or "Custom"

        with self.storeLock:
            if len(self.itemsById) >= maxManualItems:
                raise ValueError("You have too many custom assignments; delete some first.")
            itemId = "manual-" + secrets.token_hex(8)
            item = {
                "id": itemId,
                "title": title,
                "course": course,
                "courseId": "manual",
                "type": "assignment",
                "dueAt": toIso(dueMoment),
                "points": coercePoints(rawItem.get("points")),
                "submitted": False,
                "missing": False,
                "url": None,
                "manual": True,
            }
            self.itemsById[itemId] = item
            self._save()
            return dict(item)

    def delete(self, itemId):
        """Remove a manual assignment by id; return True if it existed."""
        itemId = coerceString(itemId)
        with self.storeLock:
            existed = self.itemsById.pop(itemId, None) is not None
            if existed:
                self._save()
            return existed

    def getAll(self):
        """All manual assignments with a due date, soonest first."""
        with self.storeLock:
            items = [dict(item) for item in self.itemsById.values() if item.get("dueAt")]
        items.sort(key=lambda item: item["dueAt"])
        return items
