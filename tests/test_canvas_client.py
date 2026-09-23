import io
import json
import unittest
from datetime import datetime, timezone
from urllib.error import HTTPError

import canvas_client


class FakeResponse(io.BytesIO):
    def __init__(self, payload, linkHeader=None):
        super().__init__(json.dumps(payload).encode("utf-8"))
        self.headers = {"Link": linkHeader} if linkHeader else {}


def plannerItem(plannableId, dueAt, plannableType="assignment", submissions=None, override=None):
    return {
        "plannable_id": plannableId,
        "plannable_type": plannableType,
        "plannable_date": dueAt,
        "context_name": "MATH 241",
        "html_url": f"/courses/1/assignments/{plannableId}",
        "plannable": {"title": f"Item {plannableId}", "due_at": dueAt, "points_possible": 10},
        "submissions": submissions if submissions is not None else {"submitted": False, "missing": False},
        "planner_override": override,
    }


class ParseNextLinkTests(unittest.TestCase):
    def test_finds_next_among_several_rels(self):
        linkHeader = ('<https://x.instructure.com/api/v1/planner/items?page=1>; rel="current",'
                      '<https://x.instructure.com/api/v1/planner/items?page=2>; rel="next",'
                      '<https://x.instructure.com/api/v1/planner/items?page=1>; rel="first"')
        self.assertEqual(canvas_client.parseNextLink(linkHeader),
                         "https://x.instructure.com/api/v1/planner/items?page=2")

    def test_no_next(self):
        self.assertIsNone(canvas_client.parseNextLink('<https://x/api?page=1>; rel="current"'))
        self.assertIsNone(canvas_client.parseNextLink(None))


class IsIncompleteTests(unittest.TestCase):
    def test_unsubmitted_assignment_is_incomplete(self):
        self.assertTrue(canvas_client.isIncomplete(plannerItem(1, "2026-09-24T05:59:00Z")))

    def test_submitted_excused_graded_are_complete(self):
        for statusKey in ("submitted", "excused", "graded"):
            self.assertFalse(canvas_client.isIncomplete(plannerItem(1, "2026-09-24T05:59:00Z", submissions={statusKey: True})))

    def test_marked_done_in_planner_is_complete(self):
        self.assertFalse(canvas_client.isIncomplete(plannerItem(1, "2026-09-24T05:59:00Z", override={"marked_complete": True})))

    def test_non_assignment_types_are_skipped(self):
        for itemType in ("calendar_event", "planner_note", "wiki_page"):
            self.assertFalse(canvas_client.isIncomplete(plannerItem(1, "2026-09-24T05:59:00Z", plannableType=itemType)))


class GetIncompleteAssignmentsTests(unittest.TestCase):
    baseUrl = "https://school.instructure.com"
    now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)

    def test_follows_pagination_filters_and_sorts(self):
        pages = [
            FakeResponse([plannerItem(3, "2026-09-26T05:59:00Z"),
                          plannerItem(4, "2026-09-25T05:59:00Z", submissions={"submitted": True})],
                         f'<{self.baseUrl}/api/v1/planner/items?page=2>; rel="next"'),
            FakeResponse([plannerItem(5, "2026-09-24T05:59:00Z"), plannerItem(3, "2026-09-26T05:59:00Z")]),
        ]
        requestedUrls = []

        def fakeOpener(apiRequest, timeout):
            requestedUrls.append(apiRequest.full_url)
            self.assertEqual(apiRequest.get_header("Authorization"), "Bearer secret")
            return pages.pop(0)

        assignmentList = canvas_client.getIncompleteAssignments(self.baseUrl, "secret", now=self.now, opener=fakeOpener)

        self.assertEqual(len(requestedUrls), 2)
        self.assertIn("filter=incomplete_items", requestedUrls[0])
        self.assertEqual([item["id"] for item in assignmentList], ["assignment-5", "assignment-3"])
        self.assertEqual(assignmentList[0]["url"], f"{self.baseUrl}/courses/1/assignments/5")
        self.assertEqual(assignmentList[0]["course"], "MATH 241")

    def test_refuses_to_follow_link_to_other_host(self):
        def fakeOpener(apiRequest, timeout):
            return FakeResponse([], '<https://evil.example.com/steal>; rel="next"')

        with self.assertRaises(canvas_client.CanvasError):
            canvas_client.getIncompleteAssignments(self.baseUrl, "secret", now=self.now, opener=fakeOpener)

    def test_bad_token_reports_401(self):
        def fakeOpener(apiRequest, timeout):
            raise HTTPError(apiRequest.full_url, 401, "Unauthorized", {}, None)

        with self.assertRaises(canvas_client.CanvasError) as raised:
            canvas_client.getIncompleteAssignments(self.baseUrl, "wrong", now=self.now, opener=fakeOpener)
        self.assertEqual(raised.exception.statusCode, 401)


class DemoDataTests(unittest.TestCase):
    def test_demo_items_have_required_fields(self):
        for item in canvas_client.makeDemoAssignments():
            for fieldName in ("id", "title", "course", "dueAt"):
                self.assertTrue(item[fieldName])


if __name__ == "__main__":
    unittest.main()
