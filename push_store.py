"""A small on-disk store for assignments pushed in from the browser userscript.

When Canvas personal access tokens are disabled, the server can't call Canvas
itself. Instead a Violentmonkey userscript running in the student's own browser
reads the assignment data Canvas already loaded onto pages they open (their grades
page, an individual assignment page) and POSTs it here. This module holds that data
between page loads and reports which items are still incomplete.

Two kinds of push are accepted:
  * a full item (has a due date) — created or updated outright;
  * a status-only update (no due date, e.g. from clicking Submit on an assignment
    page) — flips the submitted/missing flags of an item already known here.

Each item records the course it belongs to (both the Canvas course id and its
name), and course names are backfilled across items so every assignment keeps a
firm, consistent link to its class even if one page didn't spell the name out.
"""

import json
import threading
from datetime import datetime, timedelta, timezone

# Keep the store honest and bounded: reject obviously bad input from the page.
maxItemsPerSync = 2000
maxStringLength = 500
allowedTypes = {"assignment", "quiz", "discussion_topic", "sub_assignment"}


def coerceString(value, fallback=""):
    if not isinstance(value, str):
        return fallback
    return value[:maxStringLength]


def coercePoints(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def normalizeFullItem(rawItem):
    """Validate a full item (with a due date) from the userscript, or return None."""
    if not isinstance(rawItem, dict):
        return None
    itemId = coerceString(rawItem.get("id"))
    dueAt = coerceString(rawItem.get("dueAt"))
    if not itemId or not dueAt:
        return None  # no id or no due date: nothing we can place on the calendar

    itemType = rawItem.get("type")
    if itemType not in allowedTypes:
        itemType = "assignment"

    return {
        "id": itemId,
        "title": coerceString(rawItem.get("title"), "Untitled"),
        "course": coerceString(rawItem.get("course")),
        "courseId": coerceString(rawItem.get("courseId")),
        "type": itemType,
        "dueAt": dueAt,
        "points": coercePoints(rawItem.get("points")),
        "submitted": bool(rawItem.get("submitted")),
        "missing": bool(rawItem.get("missing")),
        "url": coerceString(rawItem.get("url")) or None,
    }


class PushStore:
    """Assignments pushed from the browser, persisted to a JSON file."""

    def __init__(self, storePath, lookbackDays=14, horizonDays=90):
        self.storePath = storePath
        self.lookbackDays = lookbackDays
        self.horizonDays = horizonDays
        self.storeLock = threading.Lock()
        self.itemsById = {}
        self.courseNames = {}  # courseId -> most recently seen course name
        self._load()

    def _load(self):
        try:
            savedData = json.loads(self.storePath.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return
        self.itemsById = {item["id"]: item for item in savedData.get("assignments", []) if isinstance(item, dict) and "id" in item}
        self.courseNames = {k: v for k, v in (savedData.get("courseNames") or {}).items() if isinstance(v, str)}

    def _save(self):
        payload = {"assignments": list(self.itemsById.values()), "courseNames": self.courseNames}
        # Write to a temp file then replace, so a crash mid-write can't corrupt the store.
        tempPath = self.storePath.with_suffix(self.storePath.suffix + ".tmp")
        tempPath.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tempPath.replace(self.storePath)

    def sync(self, rawItems, now=None):
        """Merge a batch of pushed items. Returns (accepted, skipped) counts."""
        if not isinstance(rawItems, list):
            raise ValueError("Expected a list of assignments.")
        now = now or datetime.now(timezone.utc)
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        accepted = 0
        skipped = 0
        with self.storeLock:
            for rawItem in rawItems[:maxItemsPerSync]:
                if self._applyOne(rawItem, stamp):
                    accepted += 1
                else:
                    skipped += 1
            self._backfillCourseNames()
            self._prune(now)
            self._save()
        return accepted, skipped

    def _applyOne(self, rawItem, stamp):
        """Apply one pushed item; return True if it was accepted."""
        if not isinstance(rawItem, dict):
            return False
        itemId = coerceString(rawItem.get("id"))
        if not itemId:
            return False

        self._recordCourseName(rawItem)

        if coerceString(rawItem.get("dueAt")):
            fullItem = normalizeFullItem(rawItem)
            if fullItem is None:
                return False
            fullItem["seenAt"] = stamp
            self.itemsById[itemId] = fullItem
            return True

        # Status-only update: flip flags on an item we already know, without a due date.
        existing = self.itemsById.get(itemId)
        if existing is None:
            return False
        if "submitted" in rawItem:
            existing["submitted"] = bool(rawItem.get("submitted"))
        if "missing" in rawItem:
            existing["missing"] = bool(rawItem.get("missing"))
        newCourse = coerceString(rawItem.get("course"))
        if newCourse:
            existing["course"] = newCourse
        existing["seenAt"] = stamp
        return True

    def _recordCourseName(self, rawItem):
        courseId = coerceString(rawItem.get("courseId"))
        courseName = coerceString(rawItem.get("course"))
        if courseId and courseName:
            self.courseNames[courseId] = courseName

    def _backfillCourseNames(self):
        """Give every item the best known name for its course id."""
        for item in self.itemsById.values():
            courseId = item.get("courseId")
            if courseId and not item.get("course") and courseId in self.courseNames:
                item["course"] = self.courseNames[courseId]

    def _prune(self, now):
        """Drop items whose due date is long past, so the store can't grow forever."""
        cutoff = now - timedelta(days=self.lookbackDays)
        for itemId in list(self.itemsById):
            dueAt = parseIso(self.itemsById[itemId].get("dueAt"))
            if dueAt is not None and dueAt < cutoff:
                del self.itemsById[itemId]

    def getIncomplete(self, now=None):
        """Incomplete items with a due date, within the window, soonest first."""
        now = now or datetime.now(timezone.utc)
        earliest = now - timedelta(days=self.lookbackDays)
        latest = now + timedelta(days=self.horizonDays)
        with self.storeLock:
            candidates = list(self.itemsById.values())

        result = []
        for item in candidates:
            if item.get("submitted"):
                continue
            dueAt = parseIso(item.get("dueAt"))
            if dueAt is None or not (earliest <= dueAt <= latest):
                continue
            publicItem = {key: item[key] for key in item if key != "seenAt"}
            result.append(publicItem)

        result.sort(key=lambda item: item["dueAt"])
        return result


def parseIso(isoString):
    """Parse an ISO 8601 timestamp (with a trailing Z) to an aware UTC datetime."""
    if not isinstance(isoString, str) or not isoString:
        return None
    try:
        parsed = datetime.fromisoformat(isoString.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
