// ==UserScript==
// @name         Canvas Assignment Tracker Sync
// @namespace    canvas-assignment-tracker
// @version      2.3.0
// @description  Reads the assignments Canvas already loaded on your grades and assignment pages and pushes them, encrypted, to your local Assignment Tracker. Submitting an assignment updates it immediately.
// @match        https://*.instructure.com/courses/*/grades*
// @match        https://*.instructure.com/courses/*/assignments/*
// @run-at       document-idle
// @grant        GM_xmlhttpRequest
// @grant        unsafeWindow
// @grant        GM_setValue
// @grant        GM_getValue
// @connect      *
// @noframes
// ==/UserScript==

/*
 * This script runs in YOUR browser, only on grades and assignment pages you open
 * yourself. It reads the assignment table Canvas rendered and window.ENV — data
 * Canvas already delivered to draw pages you're authorized to see — and sends
 * "assignment X, in course Y, due then, submitted or not" to your tracker.
 *
 * Traffic is signed with a token only your tracker knows. @connect is "*" so it can
 * reach your tracker at whatever URL you host it (localhost or a public domain); the
 * token is only ever sent to trackerBase, which you set below.
 *
 * ---- SET THESE TWO ----
 */
const trackerBase = "https://localhost:8000";   // your tracker's URL (e.g. https://tracker.yourdomain.com)
const syncToken = "PASTE_YOUR_SYNC_TOKEN_HERE"; // the SYNC_TOKEN the server/.env uses

// The sync button is draggable and remembers where you put it. To force a fixed
// spot instead, set pillFixedPos to {left, top} in pixels (e.g. {left: 20, top: 20});
// leave it null to drag-and-remember.
const pillFixedPos = null;

"use strict";

const debug = true; // logs a one-line summary to the browser console; set false to quiet it

// Canvas puts its data on the PAGE's window (window.ENV). Violentmonkey runs this
// script in an isolated world with its own empty window, so we reach the page's real
// window through unsafeWindow. The DOM is shared, so document queries need no change.
const pageWindow = (typeof unsafeWindow !== "undefined") ? unsafeWindow : window;

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
  // The visible course crumb is the most reliable name for the class.
  const crumb = document.querySelector("#breadcrumbs li:nth-of-type(2) .ellipsible");
  return (
    (crumb && crumb.textContent.trim()) ||
    (env && env.current_context && env.current_context.name) ||
    (env && (env.COURSE_NAME || env.course_name || env.context_name)) ||
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

// ---------- page: Grades ----------
// Names come from the rendered #grades_summary table; due dates, points and
// submitted status come from window.ENV, matched to each row by assignment id.

function collectFromGradesPage() {
  const env = pageWindow.ENV || {};
  const courseId = courseIdFromUrl();
  const course = courseName(env);

  const dueById = new Map();
  const pointsById = new Map();
  const typeById = new Map();
  for (const group of env.assignment_groups || []) {
    for (const assignment of group.assignments || []) {
      const key = String(assignment.id);
      dueById.set(key, assignment.due_at || null);
      pointsById.set(key, typeof assignment.points_possible === "number" ? assignment.points_possible : null);
      typeById.set(key, assignmentType(assignment));
    }
  }
  const submissionById = new Map();
  for (const submission of env.submissions || []) {
    submissionById.set(String(submission.assignment_id), submission);
  }

  const items = [];
  for (const row of document.querySelectorAll("#grades_summary tr.student_assignment")) {
    const link = row.querySelector('th.title a[href*="/assignments/"]');
    const idSpan = row.querySelector(".assignment_id");
    let assignmentId = idSpan ? idSpan.textContent.trim() : "";
    if (!assignmentId && link) {
      const match = (link.getAttribute("href") || "").match(/\/assignments\/(\d+)/);
      assignmentId = match ? match[1] : "";
    }
    if (!assignmentId) continue;

    const dueIso = dueById.get(assignmentId);
    if (!dueIso) continue; // no due date -> can't place it on the calendar; skip

    const submission = submissionById.get(assignmentId);
    let submitted;
    if (submission) {
      submitted = submissionLooksSubmitted(submission);
    } else {
      // Fall back to the status Canvas printed in the row.
      const statusText = (row.querySelector(".submission_status") || {}).textContent || "";
      submitted = submittedStates.has(statusText.trim());
    }

    const title = (link && link.textContent.trim()) ||
      (row.querySelector(".asset_processors_cell") || {}).dataset?.assignmentName || "Untitled";

    items.push({
      id: `assignment-${assignmentId}`,
      title,
      course,
      courseId,
      type: typeById.get(assignmentId) || "assignment",
      dueAt: new Date(dueIso).toISOString(),
      points: pointsById.has(assignmentId) ? pointsById.get(assignmentId) : null,
      submitted,
      missing: submission ? !!submission.missing : false,
      url: link ? new URL(link.getAttribute("href"), location.origin).href
                : (courseId ? `${location.origin}/courses/${courseId}/assignments/${assignmentId}` : null),
    });
  }

  // Fallback: no table rows found but ENV has the data — push without names.
  if (!items.length) {
    for (const [assignmentId, dueIso] of dueById) {
      if (!dueIso) continue;
      const submission = submissionById.get(assignmentId);
      items.push({
        id: `assignment-${assignmentId}`,
        title: `Assignment ${assignmentId}`,
        course,
        courseId,
        type: typeById.get(assignmentId) || "assignment",
        dueAt: new Date(dueIso).toISOString(),
        points: pointsById.get(assignmentId) ?? null,
        submitted: submissionLooksSubmitted(submission),
        missing: submission ? !!submission.missing : false,
        url: courseId ? `${location.origin}/courses/${courseId}/assignments/${assignmentId}` : null,
      });
    }
  }
  return items;
}

// ---------- page: single Assignment (Assignments 2.0 / React) ----------
// ENV has the id, points and course, but not the due date, so this pushes a
// status-only update: it never marks something un-submitted (that stays the grades
// page's job), only confirms a submission when we can clearly see one.

function readAssignmentDueIso() {
  const timeNode = document.querySelector("time[datetime]");
  if (timeNode && timeNode.getAttribute("datetime")) {
    const parsed = new Date(timeNode.getAttribute("datetime"));
    if (!isNaN(parsed)) return parsed.toISOString();
  }
  return null;
}

function assignmentPageLooksSubmitted() {
  // Conservative: only true when the page clearly shows a completed submission.
  const bodyText = document.body.innerText || "";
  return /\bSubmitted!\b/i.test(bodyText) || /\bTurned In\b/i.test(bodyText) ||
    !!document.querySelector('[data-testid="submission-workflow-tracker-title"]');
}

function collectFromAssignmentPage() {
  const env = pageWindow.ENV || {};
  const courseId = courseIdFromUrl();
  const assignmentId = String(env.ASSIGNMENT_ID || assignmentIdFromUrl() || "");
  if (!assignmentId) return null;

  const heading = document.querySelector("h1.title, h1, .assignment-title");
  const item = {
    id: `assignment-${assignmentId}`,
    title: (heading && heading.textContent.trim()) || "Untitled",
    course: courseName(env),
    courseId,
    type: "assignment",
    dueAt: readAssignmentDueIso(), // usually null on A2 -> status-only update
    points: typeof env.ASSIGNMENT_POINTS_POSSIBLE === "number" ? env.ASSIGNMENT_POINTS_POSSIBLE : null,
    url: courseId ? `${location.origin}/courses/${courseId}/assignments/${assignmentId}` : null,
  };
  // Only assert submitted=true when we can see it; never push submitted=false here.
  if (assignmentPageLooksSubmitted()) item.submitted = true;
  return item;
}

// When the student submits, mark it done straight away (the grades page confirms later).
function hookSubmitButtons() {
  const courseId = courseIdFromUrl();
  const assignmentId = String((pageWindow.ENV || {}).ASSIGNMENT_ID || assignmentIdFromUrl() || "");
  if (!assignmentId) return;

  const markSubmitted = () => pushItems([{
    id: `assignment-${assignmentId}`,
    course: courseName(pageWindow.ENV),
    courseId,
    submitted: true, // status-only: flips the flag on the item the grades page loaded
  }]).then(() => setPill("✓ Marked submitted", "#e94560")).catch(() => {});

  // Classic submission forms have ids beginning "submit_".
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (form && typeof form.id === "string" && form.id.startsWith("submit_")) setTimeout(markSubmitted, 500);
  }, true);

  // Assignments 2.0 uses React buttons; catch a click whose label looks like a submit.
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button, [role='button'], a.btn");
    if (!button) return;
    const label = (button.textContent || "").trim().toLowerCase();
    if (label === "submit assignment" || label === "submit" || label === "turn in" || label === "re-submit assignment") {
      setTimeout(markSubmitted, 1200);
    }
  }, true);
}

// ---------- push to the local tracker (token-signed) ----------

function pushItems(items) {
  return new Promise((resolve, reject) => {
    GM_xmlhttpRequest({
      method: "POST",
      url: `${trackerBase}/api/sync`,
      headers: { "Content-Type": "application/json", "Authorization": `Bearer ${syncToken}` },
      data: JSON.stringify({ assignments: items }),
      timeout: 10000,
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
      onerror: () => reject(new Error("tracker not reachable")),
      ontimeout: () => reject(new Error("tracker timed out")),
    });
  });
}

// ---------- small on-page status pill (draggable, position remembered) ----------

const pillPosKey = "catracker_pill_pos";

function loadPillPos() {
  if (pillFixedPos) return pillFixedPos;
  try {
    if (typeof GM_getValue === "function") {
      const stored = GM_getValue(pillPosKey);
      if (stored) return JSON.parse(stored);
    }
  } catch (e) { /* ignore */ }
  try {
    const stored = localStorage.getItem(pillPosKey);
    if (stored) return JSON.parse(stored);
  } catch (e) { /* ignore */ }
  return null;
}

function savePillPos(pos) {
  if (pillFixedPos) return; // fixed by config: don't remember drags
  const serialized = JSON.stringify(pos);
  try { if (typeof GM_setValue === "function") GM_setValue(pillPosKey, serialized); } catch (e) { /* ignore */ }
  try { localStorage.setItem(pillPosKey, serialized); } catch (e) { /* ignore */ }
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(value, max));
}

function placePill(node) {
  const pos = loadPillPos();
  if (pos && typeof pos.left === "number" && typeof pos.top === "number") {
    // Keep it on-screen even if the window is now smaller than when it was saved.
    const left = clamp(pos.left, 4, Math.max(4, window.innerWidth - node.offsetWidth - 4));
    const top = clamp(pos.top, 4, Math.max(4, window.innerHeight - node.offsetHeight - 4));
    Object.assign(node.style, { left: `${left}px`, top: `${top}px`, right: "auto", bottom: "auto" });
  } else {
    Object.assign(node.style, { right: "16px", bottom: "16px", left: "auto", top: "auto" });
  }
}

// Drag to move; a plain click (no real movement) still runs a sync.
function makeDraggable(node) {
  let dragging = false, moved = false, startX = 0, startY = 0, originLeft = 0, originTop = 0;

  node.addEventListener("pointerdown", (event) => {
    if (pillFixedPos) return;      // position locked by config
    if (event.button !== 0) return;
    dragging = true;
    moved = false;
    const rect = node.getBoundingClientRect();
    originLeft = rect.left;
    originTop = rect.top;
    startX = event.clientX;
    startY = event.clientY;
    showPill(); // keep it visible while dragging
    // Switch from right/bottom anchoring to left/top so we can move it freely.
    Object.assign(node.style, { left: `${rect.left}px`, top: `${rect.top}px`, right: "auto", bottom: "auto" });
    try { node.setPointerCapture(event.pointerId); } catch (e) { /* ignore */ }
  });

  node.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    const dx = event.clientX - startX;
    const dy = event.clientY - startY;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) moved = true;
    const left = clamp(originLeft + dx, 4, window.innerWidth - node.offsetWidth - 4);
    const top = clamp(originTop + dy, 4, window.innerHeight - node.offsetHeight - 4);
    node.style.left = `${left}px`;
    node.style.top = `${top}px`;
  });

  const endDrag = (event) => {
    if (!dragging) return;
    dragging = false;
    node.dataset.justDragged = moved ? "1" : "";
    if (moved) {
      const rect = node.getBoundingClientRect();
      savePillPos({ left: rect.left, top: rect.top });
    }
    try { node.releasePointerCapture(event.pointerId); } catch (e) { /* ignore */ }
  };
  node.addEventListener("pointerup", endDrag);
  node.addEventListener("pointercancel", endDrag);
}

// After a few seconds of inactivity the pill fades out and is removed from the page
// entirely, so it can never sit invisibly over a button and block clicks. It comes
// back on its own the next time there's something to show (a sync or status update).
const pillFadeMs = 5000;
const pillFadeAnimMs = 500; // must match the CSS opacity transition below
let fadeTimer = null;
let removeTimer = null;

function showPill() {
  if (!pillNode) return;
  if (fadeTimer) { clearTimeout(fadeTimer); fadeTimer = null; }
  if (removeTimer) { clearTimeout(removeTimer); removeTimer = null; }
  pillNode.style.opacity = "1";
  pillNode.style.pointerEvents = "auto";
}

function removePill() {
  if (pillNode && pillNode.parentNode) pillNode.parentNode.removeChild(pillNode);
  pillNode = null;
  fadeTimer = removeTimer = null;
}

function scheduleFade() {
  showPill();
  fadeTimer = setTimeout(() => {
    if (!pillNode) return;
    pillNode.style.opacity = "0";
    pillNode.style.pointerEvents = "none"; // stop blocking clicks even during the fade
    removeTimer = setTimeout(removePill, pillFadeAnimMs);
  }, pillFadeMs);
}

let pillNode = null;
function ensurePill() {
  if (pillNode) return pillNode;
  pillNode = document.createElement("button");
  Object.assign(pillNode.style, {
    position: "fixed", zIndex: 99999,
    padding: "8px 12px", borderRadius: "4px", border: "0",
    cursor: pillFixedPos ? "pointer" : "grab", touchAction: "none",
    font: "700 12px/1 Consolas, monospace", color: "#ffffff", background: "#e94560",
    opacity: "1", transition: "opacity 0.5s ease",
  });
  pillNode.textContent = "Sync assignments";
  pillNode.addEventListener("click", () => {
    // Don't fire a sync when the click was actually the end of a drag.
    if (pillNode.dataset.justDragged) { pillNode.dataset.justDragged = ""; return; }
    runSync();
  });
  document.body.appendChild(pillNode);
  placePill(pillNode);
  makeDraggable(pillNode);
  scheduleFade();
  return pillNode;
}

function setPill(text, background) {
  const pill = ensurePill();
  pill.textContent = text;
  if (background) pill.style.background = background;
  placePill(pill); // re-clamp after width changes with the new text
  scheduleFade();  // reset the fade countdown on every status update
}

const onGradesPage = () => /\/grades\/?$/.test(location.pathname);

async function runSync() {
  const items = onGradesPage() ? collectFromGradesPage()
    : (assignmentIdFromUrl() ? [collectFromAssignmentPage()].filter(Boolean) : null);

  if (debug) console.log("[tracker-sync]", location.pathname, "-> items:", items);

  if (!items || !items.length) {
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
if (assignmentIdFromUrl() && !onGradesPage()) hookSubmitButtons();

// Sync shortly after load, and retry once if the page's data wasn't ready yet.
setTimeout(runSync, 800);
setTimeout(() => { if (onGradesPage()) runSync(); }, 3000);
