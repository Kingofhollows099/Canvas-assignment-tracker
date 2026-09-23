// ==UserScript==
// @name         Canvas Assignment Tracker Sync
// @namespace    canvas-assignment-tracker
// @version      2.0.0
// @description  Reads the assignments Canvas already loaded on your grades and assignment pages and pushes them, encrypted, to your local Assignment Tracker. Submitting an assignment updates it immediately.
// @match        https://*.instructure.com/courses/*/grades*
// @match        https://*.instructure.com/courses/*/assignments/*
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @connect      localhost
// @connect      127.0.0.1
// @noframes
// ==/UserScript==

/*
 * This script runs in YOUR browser, only on grades and assignment pages you open
 * yourself. It reads window.ENV and the page's own contents — the data Canvas
 * already delivered to draw pages you're authorized to see — and sends
 * "assignment X, in course Y, due then, submitted or not" to your local tracker.
 *
 * It stores no login and keeps no cookie. Traffic is encrypted with TLS (https)
 * and signed with a token only your tracker knows, so nothing else on your machine
 * can read or forge these pushes. If your tracker isn't running, pushes fail quietly.
 *
 * ---- SET THESE TWO to what the server prints on startup ----
 */
const trackerBase = "https://localhost:8000"; // must match the scheme+port the server prints
const syncToken = "PASTE_YOUR_SYNC_TOKEN_HERE";

"use strict";

// ---------- generic helpers ----------

function courseIdFromUrl() {
  const match = location.pathname.match(/\/courses\/(\d+)(?:\/|$)/);
  return match ? match[1] : null;
}

function assignmentIdFromUrl() {
  const match = location.pathname.match(/\/assignments\/(\d+)(?:\/|$)/);
  return match ? match[1] : null;
}

function courseName(env) {
  // Prefer Canvas' own value; fall back to the course crumb, which names the class.
  const crumb = document.querySelector("#breadcrumbs li:nth-of-type(2) .ellipsible");
  return (
    (env && (env.COURSE_NAME || env.course_name || env.context_name)) ||
    (crumb && crumb.textContent.trim()) ||
    ""
  );
}

// Canvas marks work as done under several workflow states; treat them all as submitted.
const submittedStates = new Set(["submitted", "graded", "pending_review", "complete", "graded_online"]);

function submissionLooksSubmitted(submission) {
  if (!submission) return false;
  if (submission.excused || submission.submitted_at) return true;
  return submittedStates.has(submission.workflow_state);
}

function assignmentType(assignment) {
  const types = (assignment && assignment.submission_types) || [];
  if (types.includes("online_quiz")) return "quiz";
  if (types.includes("discussion_topic")) return "discussion_topic";
  return "assignment";
}

// ---------- page: Grades (the whole-course list, from structured ENV data) ----------

function collectFromGradesPage() {
  const env = window.ENV;
  if (!env || !Array.isArray(env.assignment_groups)) return null;

  const courseId = courseIdFromUrl();
  const course = courseName(env);
  const submissionByAssignment = new Map();
  for (const submission of env.submissions || []) {
    submissionByAssignment.set(String(submission.assignment_id), submission);
  }

  const items = [];
  for (const group of env.assignment_groups) {
    for (const assignment of group.assignments || []) {
      if (!assignment.due_at) continue;
      const submission = submissionByAssignment.get(String(assignment.id));
      items.push({
        id: `assignment-${assignment.id}`,
        title: assignment.name || "Untitled",
        course,
        courseId,
        type: assignmentType(assignment),
        dueAt: new Date(assignment.due_at).toISOString(),
        points: typeof assignment.points_possible === "number" ? assignment.points_possible : null,
        submitted: submissionLooksSubmitted(submission),
        missing: !!(submission && submission.missing),
        url: assignment.html_url ||
          (courseId ? `${location.origin}/courses/${courseId}/assignments/${assignment.id}` : null),
      });
    }
  }
  return items;
}

// ---------- page: single Assignment ----------

function readAssignmentDueIso(env) {
  const fromEnv = env && env.ASSIGNMENT && env.ASSIGNMENT.due_at;
  if (fromEnv) return new Date(fromEnv).toISOString();
  // A machine-readable date Canvas leaves in the DOM, if present.
  const timeNode = document.querySelector(".assignment .due_date_display time[datetime], time[datetime]");
  if (timeNode && timeNode.getAttribute("datetime")) {
    const parsed = new Date(timeNode.getAttribute("datetime"));
    if (!isNaN(parsed)) return parsed.toISOString();
  }
  return null; // couldn't read a due date reliably: we'll send a status-only update
}

function assignmentLooksSubmitted(env) {
  const submission = (env && (env.SUBMISSION || env.submission)) || null;
  if (submissionLooksSubmitted(submission)) return true;
  // Fall back to what the page shows in the sidebar.
  const sidebar = document.querySelector("#sidebar_content, .assignment-submission, .submission-details, #assignment_show");
  const text = (sidebar ? sidebar.textContent : "").toLowerCase();
  return text.includes("submitted!") || text.includes("turned in") || text.includes("submission is now checked");
}

function collectFromAssignmentPage() {
  const env = window.ENV;
  const courseId = courseIdFromUrl();
  const assignmentId = assignmentIdFromUrl();
  if (!assignmentId) return null;

  const title =
    (env && env.ASSIGNMENT && env.ASSIGNMENT.name) ||
    (document.querySelector("h1.title, .assignment-title, h1") || {}).textContent?.trim() ||
    "Untitled";

  return {
    id: `assignment-${assignmentId}`,
    title,
    course: courseName(env),
    courseId,
    type: "assignment",
    dueAt: readAssignmentDueIso(env), // may be null -> status-only update on the server
    points: env && env.ASSIGNMENT && typeof env.ASSIGNMENT.points_possible === "number"
      ? env.ASSIGNMENT.points_possible : null,
    submitted: assignmentLooksSubmitted(env),
    missing: false,
    url: `${location.origin}/courses/${courseId}/assignments/${assignmentId}`,
  };
}

// When the student submits, update the tracker straight away (the reload then confirms).
function hookSubmitButtons() {
  const courseId = courseIdFromUrl();
  const assignmentId = assignmentIdFromUrl();
  if (!assignmentId) return;

  const markSubmitted = () => {
    pushItems([{
      id: `assignment-${assignmentId}`,
      course: courseName(window.ENV),
      courseId,
      submitted: true, // status-only: no dueAt, so the server just flips the flag
    }]).then(() => setPill("✓ Marked submitted", "#e94560")).catch(() => {});
  };

  // Canvas' submission forms all have ids beginning "submit_"; quizzes use a start link.
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (form && typeof form.id === "string" && form.id.startsWith("submit_")) {
      setTimeout(markSubmitted, 400);
    }
  }, true);
  const quizStart = document.querySelector("#take_quiz_link, a.take_quiz_link");
  if (quizStart) quizStart.addEventListener("click", () => setTimeout(markSubmitted, 400));
}

// ---------- push to the local tracker (encrypted + token-signed) ----------

function pushItems(items) {
  return new Promise((resolve, reject) => {
    GM_xmlhttpRequest({
      method: "POST",
      url: `${trackerBase}/api/sync`,
      headers: { "Content-Type": "application/json", "Authorization": `Bearer ${syncToken}` },
      data: JSON.stringify({ assignments: items }),
      timeout: 8000,
      onload: (response) => {
        if (response.status >= 200 && response.status < 300) {
          let parsed = {};
          try { parsed = JSON.parse(response.responseText); } catch (e) { /* ignore */ }
          resolve(parsed);
        } else if (response.status === 401) {
          reject(new Error("tracker rejected the token"));
        } else {
          reject(new Error(`tracker returned ${response.status}`));
        }
      },
      onerror: () => reject(new Error("tracker not reachable / cert not trusted")),
      ontimeout: () => reject(new Error("tracker timed out")),
    });
  });
}

// ---------- small on-page status pill ----------

let pillNode = null;
function ensurePill() {
  if (pillNode) return pillNode;
  pillNode = document.createElement("button");
  Object.assign(pillNode.style, {
    position: "fixed", right: "16px", bottom: "16px", zIndex: 99999,
    padding: "8px 12px", borderRadius: "4px", border: "0", cursor: "pointer",
    font: "700 12px/1 Consolas, monospace", color: "#ffffff", background: "#e94560",
  });
  pillNode.textContent = "Sync assignments";
  pillNode.addEventListener("click", runSync);
  document.body.appendChild(pillNode);
  return pillNode;
}

function setPill(text, background) {
  const pill = ensurePill();
  pill.textContent = text;
  if (background) pill.style.background = background;
}

async function runSync() {
  const onGrades = /\/grades\/?$/.test(location.pathname) || location.pathname.endsWith("/grades");
  const items = onGrades ? collectFromGradesPage() : (assignmentIdFromUrl() ? [collectFromAssignmentPage()] : null);

  if (!items || !items.length || !items[0]) {
    setPill("⚠ No Canvas data on this page", "#252535");
    return;
  }
  setPill(`Syncing ${items.length}...`, "#252535");
  try {
    const result = await pushItems(items);
    const done = items.filter((item) => item.submitted).length;
    setPill(`✓ Synced ${result.accepted ?? items.length} (${done} done)`, "#e94560");
  } catch (error) {
    setPill(`⚠ ${error.message}`, "#252535");
  }
}

// ---------- start ----------

ensurePill();
if (assignmentIdFromUrl() && !/\/grades/.test(location.pathname)) {
  hookSubmitButtons();
}
setTimeout(runSync, 800);
