// ==UserScript==
// @name         Canvas Assignment Tracker Sync
// @namespace    canvas-assignment-tracker
// @version      1.0.0
// @description  Reads the assignments Canvas already loaded on your grades page and pushes them to your local Assignment Tracker, so submitted work drops off automatically.
// @match        https://*.instructure.com/courses/*/grades*
// @match        https://*.instructure.com/courses/*/grades
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @connect      localhost
// @connect      127.0.0.1
// @noframes
// ==/UserScript==

/*
 * This script runs in YOUR browser, only on grades pages you open yourself.
 * It reads window.ENV — the data Canvas already delivered to render your grades,
 * which you are authorized to see — and sends "assignment X, due then, submitted
 * or not" to http://localhost:8000. It stores no login, keeps no cookie, and does
 * nothing while you are not on the page. If your local tracker isn't running,
 * the push simply fails quietly.
 */

"use strict";

const trackerBase = "http://localhost:8000"; // change if you run the server on another port

// ---- read Canvas' own page data -------------------------------------------

function courseIdFromUrl() {
  const match = location.pathname.match(/\/courses\/(\d+)\//);
  return match ? match[1] : null;
}

function courseName(env) {
  return (
    env.COURSE_NAME ||
    env.course_name ||
    env.context_name ||
    (document.querySelector("#breadcrumbs .ellipsible") || {}).textContent?.trim() ||
    document.title.replace(/\s*[:|-].*$/, "").trim() ||
    ""
  );
}

// Canvas marks work as done under several workflow states; treat them all as submitted.
const submittedStates = new Set(["submitted", "graded", "pending_review", "complete", "graded_online"]);

function isSubmitted(submission) {
  if (!submission) return false;
  if (submission.excused) return true;
  if (submission.submitted_at) return true;
  return submittedStates.has(submission.workflow_state);
}

function collectAssignments() {
  const env = window.ENV;
  if (!env || !Array.isArray(env.assignment_groups)) {
    return { items: [], reason: "Couldn't find Canvas assignment data on this page." };
  }

  const courseId = courseIdFromUrl();
  const course = courseName(env);

  // ENV.submissions: one entry per assignment, with the student's submission state.
  const submissionByAssignment = new Map();
  for (const submission of env.submissions || []) {
    submissionByAssignment.set(String(submission.assignment_id), submission);
  }

  const items = [];
  for (const group of env.assignment_groups) {
    for (const assignment of group.assignments || []) {
      if (!assignment.due_at) continue; // no due date: nothing to place on the calendar
      const submission = submissionByAssignment.get(String(assignment.id));
      items.push({
        id: `assignment-${assignment.id}`,
        title: assignment.name || "Untitled",
        course,
        type: assignmentType(assignment),
        dueAt: new Date(assignment.due_at).toISOString(),
        points: typeof assignment.points_possible === "number" ? assignment.points_possible : null,
        submitted: isSubmitted(submission),
        missing: !!(submission && submission.missing),
        url:
          assignment.html_url ||
          (courseId ? `${location.origin}/courses/${courseId}/assignments/${assignment.id}` : null),
      });
    }
  }
  return { items, reason: items.length ? "" : "No assignments with due dates on this page." };
}

function assignmentType(assignment) {
  const types = assignment.submission_types || [];
  if (types.includes("online_quiz")) return "quiz";
  if (types.includes("discussion_topic")) return "discussion_topic";
  return "assignment";
}

// ---- push to the local tracker --------------------------------------------

function pushAssignments(items) {
  return new Promise((resolve, reject) => {
    GM_xmlhttpRequest({
      method: "POST",
      url: `${trackerBase}/api/sync`,
      headers: { "Content-Type": "application/json" },
      data: JSON.stringify({ assignments: items }),
      timeout: 8000,
      onload: (response) => {
        if (response.status >= 200 && response.status < 300) {
          let parsed = {};
          try { parsed = JSON.parse(response.responseText); } catch (e) { /* ignore */ }
          resolve(parsed);
        } else {
          reject(new Error(`tracker returned ${response.status}`));
        }
      },
      onerror: () => reject(new Error("tracker not reachable")),
      ontimeout: () => reject(new Error("tracker timed out")),
    });
  });
}

// ---- small on-page status pill --------------------------------------------

function makePill() {
  const pill = document.createElement("button");
  Object.assign(pill.style, {
    position: "fixed", right: "16px", bottom: "16px", zIndex: 99999,
    padding: "8px 12px", borderRadius: "4px", border: "0", cursor: "pointer",
    font: "700 12px/1 Consolas, monospace", color: "#ffffff", background: "#e94560",
  });
  pill.textContent = "Sync assignments";
  document.body.appendChild(pill);
  return pill;
}

function setPill(pill, text, background) {
  pill.textContent = text;
  pill.style.background = background;
}

async function runSync(pill) {
  const { items, reason } = collectAssignments();
  if (!items.length) {
    setPill(pill, `⚠ ${reason}`, "#252535");
    return;
  }
  setPill(pill, `Syncing ${items.length}...`, "#252535");
  try {
    const result = await pushAssignments(items);
    const done = items.filter((item) => item.submitted).length;
    setPill(pill, `✓ Synced ${result.accepted ?? items.length} (${done} done)`, "#e94560");
  } catch (error) {
    setPill(pill, `⚠ ${error.message}`, "#252535");
  }
}

const pill = makePill();
pill.addEventListener("click", () => runSync(pill));
// Sync once automatically when the grades page settles.
setTimeout(() => runSync(pill), 800);
