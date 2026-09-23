"""Canvas LMS API client: fetches the current user's incomplete assignments.

Uses the Planner API (/api/v1/planner/items) because it returns items from
every course the user is enrolled in with a single paginated request, and it
already knows about submissions and "mark as done" overrides.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

# Planner item types that represent graded work a student has to turn in.
# Calendar events, planner notes and wiki pages are left out on purpose.
assignmentTypes = {"assignment", "quiz", "discussion_topic", "sub_assignment"}

# Hard stop on pagination so a misbehaving server can't loop us forever.
maxPages = 50

linkNextPattern = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


class CanvasError(Exception):
    """A failure talking to Canvas, with a message safe to show the user."""

    def __init__(self, message, statusCode=502):
        super().__init__(message)
        self.statusCode = statusCode


def parseNextLink(linkHeader):
    """Return the rel="next" URL from a Canvas Link header, or None."""
    if not linkHeader:
        return None
    for linkPart in linkHeader.split(","):
        linkMatch = linkNextPattern.search(linkPart)
        if linkMatch:
            return linkMatch.group(1)
    return None


def isoUtc(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetchPlannerItems(baseUrl, apiToken, startDate, endDate, opener=urlopen):
    """Fetch every planner item between startDate and endDate, following pagination."""
    queryString = urlencode({
        "start_date": isoUtc(startDate),
        "end_date": isoUtc(endDate),
        "filter": "incomplete_items",
        "per_page": 100,
    })
    nextUrl = f"{baseUrl}/api/v1/planner/items?{queryString}"
    expectedHost = urlparse(baseUrl).netloc
    allItems = []

    for _ in range(maxPages):
        # Never send the token to a host other than the configured Canvas
        # instance, even if a Link header points somewhere else.
        if urlparse(nextUrl).netloc != expectedHost:
            raise CanvasError("Canvas returned a pagination link to an unexpected host.")

        apiRequest = Request(nextUrl, headers={
            "Authorization": f"Bearer {apiToken}",
            "Accept": "application/json",
        })
        try:
            with opener(apiRequest, timeout=20) as apiResponse:
                pageItems = json.load(apiResponse)
                nextUrl = parseNextLink(apiResponse.headers.get("Link"))
        except HTTPError as httpError:
            if httpError.code == 401:
                raise CanvasError("Canvas rejected the API token (401). Check CANVAS_API_TOKEN.", 401)
            if httpError.code == 404:
                raise CanvasError("Canvas planner API not found (404). Check CANVAS_BASE_URL.", 502)
            raise CanvasError(f"Canvas returned HTTP {httpError.code}.", 502)
        except URLError as urlError:
            raise CanvasError(f"Could not reach Canvas: {urlError.reason}", 502)
        except json.JSONDecodeError:
            raise CanvasError("Canvas returned something that isn't JSON. Check CANVAS_BASE_URL.", 502)

        if not isinstance(pageItems, list):
            raise CanvasError("Unexpected response shape from the Canvas planner API.", 502)
        allItems.extend(pageItems)
        if not nextUrl:
            break

    return allItems


def isIncomplete(plannerItem):
    """True when a planner item is graded work the user still has to do."""
    if plannerItem.get("plannable_type") not in assignmentTypes:
        return False

    plannerOverride = plannerItem.get("planner_override") or {}
    if plannerOverride.get("marked_complete") or plannerOverride.get("dismissed"):
        return False

    # "submissions" is either false (not submittable) or a status object.
    submissionStatus = plannerItem.get("submissions") or {}
    if submissionStatus.get("submitted") or submissionStatus.get("excused") or submissionStatus.get("graded"):
        return False

    return True


def normalizeItem(plannerItem, baseUrl):
    """Reduce a raw planner item to the few fields the web UI needs."""
    plannable = plannerItem.get("plannable") or {}
    submissionStatus = plannerItem.get("submissions") or {}
    dueAt = plannable.get("due_at") or plannable.get("todo_date") or plannerItem.get("plannable_date")
    htmlUrl = plannerItem.get("html_url") or ""

    return {
        "id": f"{plannerItem.get('plannable_type')}-{plannerItem.get('plannable_id')}",
        "title": plannable.get("title") or plannable.get("name") or "Untitled",
        "course": plannerItem.get("context_name") or "",
        "type": plannerItem.get("plannable_type"),
        "dueAt": dueAt,
        "points": plannable.get("points_possible"),
        "missing": bool(submissionStatus.get("missing")),
        # Planner html_url values are site-relative ("/courses/1/assignments/2").
        "url": urljoin(baseUrl + "/", htmlUrl) if htmlUrl else None,
    }


def getIncompleteAssignments(baseUrl, apiToken, lookbackDays=14, horizonDays=60, now=None, opener=urlopen):
    """Incomplete assignments due from lookbackDays ago to horizonDays ahead, soonest first."""
    now = now or datetime.now(timezone.utc)
    plannerItems = fetchPlannerItems(
        baseUrl, apiToken,
        now - timedelta(days=lookbackDays),
        now + timedelta(days=horizonDays),
        opener=opener,
    )
    assignmentList = [normalizeItem(item, baseUrl) for item in plannerItems if isIncomplete(item)]
    assignmentList = [item for item in assignmentList if item["dueAt"]]

    # The same assignment can appear twice (e.g. a graded discussion and its
    # assignment); keep the first copy of each id.
    seenIds = set()
    uniqueAssignments = []
    for item in assignmentList:
        if item["id"] not in seenIds:
            seenIds.add(item["id"])
            uniqueAssignments.append(item)

    uniqueAssignments.sort(key=lambda item: item["dueAt"])
    return uniqueAssignments


def makeDemoAssignments(now=None):
    """Sample data so the UI can be tried without a Canvas account."""
    now = now or datetime.now(timezone.utc)
    localNow = now.astimezone()
    todayMidnight = localNow.replace(hour=0, minute=0, second=0, microsecond=0)

    def dueAt(dayOffset, hour, minute=0):
        return isoUtc(todayMidnight + timedelta(days=dayOffset, hours=hour, minutes=minute))

    demoRows = [
        (-2, 23, 59, "Lab 3: Pendulum Report", "PHYS 201", "assignment", 20, True),
        (0, 9, 0, "Reading Quiz: Chapter 7", "HIST 110", "quiz", 10, False),
        (0, 23, 59, "Problem Set 5", "MATH 241", "assignment", 25, False),
        (1, 12, 0, "Discussion: Primary Sources", "HIST 110", "discussion_topic", 5, False),
        (2, 17, 0, "Project Proposal", "CS 350", "assignment", 50, False),
        (2, 23, 59, "Lab 4 Pre-lab", "PHYS 201", "quiz", 5, False),
        (4, 8, 30, "Midterm Review Worksheet", "MATH 241", "assignment", 0, False),
        (5, 23, 59, "Essay Draft", "ENGL 102", "assignment", 100, False),
        (6, 14, 0, "Code Review", "CS 350", "assignment", 15, False),
        (9, 23, 59, "Problem Set 6", "MATH 241", "assignment", 25, False),
        (12, 23, 59, "Final Essay", "ENGL 102", "assignment", 150, False),
    ]
    return [
        {
            "id": f"demo-{rowIndex}",
            "title": title,
            "course": course,
            "type": itemType,
            "dueAt": dueAt(dayOffset, hour, minute),
            "points": points,
            "missing": missing,
            "url": None,
        }
        for rowIndex, (dayOffset, hour, minute, title, course, itemType, points, missing) in enumerate(demoRows)
    ]
