"use strict";

const refetchMs = 15 * 60 * 1000; // server caches for 5 min; poll Canvas lightly
const rerenderMs = 60 * 1000;     // keeps "in 3h" labels and the "today" column current

const state = { assignments: [], fetchedAt: null, demo: false, mode: "canvas", view: "calendar", error: null };

let tooltipAnchor = null; // element the tooltip is currently showing for

const dayMs = 24 * 60 * 60 * 1000;
const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
const weekdayFmt = new Intl.DateTimeFormat(undefined, { weekday: "short" });
const monthDayFmt = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });
const longDayFmt = new Intl.DateTimeFormat(undefined, { weekday: "long", month: "short", day: "numeric" });

// ---------- small helpers ----------

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text; // textContent: Canvas titles are never parsed as HTML
  return node;
}

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function addDays(date, days) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);
}

function dayKey(date) {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function relativeDue(dueDate, now) {
  const diffMin = Math.round((dueDate - now) / 60000);
  const absMin = Math.abs(diffMin);
  let span;
  if (absMin < 60) span = `${absMin}m`;
  else if (absMin < 48 * 60) span = `${Math.round(absMin / 60)}h`;
  else span = `${Math.round(absMin / 1440)}d`;
  return diffMin < 0 ? `${span} overdue` : `in ${span}`;
}

function pointsLabel(points) {
  if (points == null) return "";
  return `${points} pt${points === 1 ? "" : "s"}`;
}

function typeLabel(type) {
  return { quiz: "Quiz", discussion_topic: "Discussion", assignment: "Assignment", sub_assignment: "Assignment" }[type] || "";
}

function setStatus(text) {
  document.getElementById("status").textContent = text;
}

// ---------- shared card ----------

function makeCard(item, now, { showTime = true } = {}) {
  const card = el(item.url ? "a" : "div", "card");
  if (item.url) {
    card.href = item.url;
    card.target = "_blank";
    card.rel = "noopener";
  }
  const dueDate = new Date(item.dueAt);
  const isPast = dueDate < now;

  if (showTime) {
    const timeLine = el("div", "card-time", timeFmt.format(dueDate));
    if (isPast || item.missing) timeLine.append(" ", el("span", "flag", "⚠"));
    card.append(timeLine);
  }
  card.append(el("div", "card-title", item.title));
  const courseLine = [item.course, typeLabel(item.type), pointsLabel(item.points)].filter(Boolean).join(" · ");
  card.append(el("div", "card-course", courseLine));

  const tipLines = [item.title, `Due ${longDayFmt.format(dueDate)}, ${timeFmt.format(dueDate)} (${relativeDue(dueDate, now)})`];
  if (item.missing) tipLines.push("Marked missing in Canvas.");
  if (item.url) tipLines.push("Click to open in Canvas.");
  card.dataset.tip = tipLines.join("\n");
  return card;
}

// ---------- calendar view ----------

function renderCalendar(now) {
  const root = document.getElementById("calendar-view");
  root.replaceChildren();
  const today = startOfDay(now);

  const byDay = new Map();
  for (const item of state.assignments) {
    const key = dayKey(new Date(item.dueAt));
    if (!byDay.has(key)) byDay.set(key, []);
    byDay.get(key).push(item);
  }

  for (let offset = 0; offset < 7; offset++) {
    const day = addDays(today, offset);
    const items = (byDay.get(dayKey(day)) || []).slice().sort((a, b) => new Date(a.dueAt) - new Date(b.dueAt));

    const column = el("article", offset === 0 ? "day today" : "day");
    const head = el("header", "day-head");
    head.append(
      el("span", "day-name", offset === 0 ? "Today" : weekdayFmt.format(day)),
      el("span", "day-date", monthDayFmt.format(day)),
    );
    if (items.length) head.append(el("span", "day-count", String(items.length)));
    column.append(head);

    const body = el("div", "day-body");
    if (items.length === 0) body.append(el("div", "day-none", "Nothing due"));
    for (const item of items) body.append(makeCard(item, now));
    column.append(body);
    root.append(column);
  }
}

// ---------- to-do view ----------

function groupLabel(dueDate, now) {
  const today = startOfDay(now);
  const dueDay = startOfDay(dueDate);
  const dayDiff = Math.round((dueDay - today) / dayMs);
  if (dueDate < now) return "⚠ Overdue";
  if (dayDiff === 0) return "Today";
  if (dayDiff === 1) return "Tomorrow";
  if (dayDiff < 7) return weekdayFmt.format(dueDate) + " " + monthDayFmt.format(dueDate);
  if (dayDiff < 14) return "Next week";
  return "Later";
}

function renderTodo(now) {
  const root = document.getElementById("todo-view");
  root.replaceChildren();

  const sorted = state.assignments.slice().sort((a, b) => new Date(a.dueAt) - new Date(b.dueAt));
  if (sorted.length === 0) {
    const message = state.mode === "userscript"
      ? "Nothing here yet. Open a course's Grades page in Canvas with the sync userscript installed to load your assignments."
      : "✓  Nothing outstanding. Enjoy it.";
    root.append(el("p", "empty", message));
    return;
  }

  // Items are already in due order, so consecutive runs share a group label.
  let currentLabel = null;
  let currentList = null;
  let currentCount = null;
  for (const item of sorted) {
    const dueDate = new Date(item.dueAt);
    const label = groupLabel(dueDate, now);
    if (label !== currentLabel) {
      currentLabel = label;
      const head = el("h2", "group-head", label);
      currentCount = el("span", "group-count", "0");
      head.append(currentCount);
      currentList = el("ol", "todo-list");
      root.append(head, currentList);
    }
    currentCount.textContent = String(Number(currentCount.textContent) + 1);

    const row = makeCard(item, now, { showTime: false });
    row.classList.add("todo-row");
    const title = row.querySelector(".card-title");
    const course = row.querySelector(".card-course");

    const when = el("div", "todo-when", relativeDue(dueDate, now));
    if (dueDate < now || item.missing) when.prepend(el("span", "flag", "⚠ "));
    when.append(el("small", null, `${monthDayFmt.format(dueDate)}, ${timeFmt.format(dueDate)}`));

    const main = el("div", "todo-main");
    main.append(title, course);
    course.textContent = [item.course, typeLabel(item.type)].filter(Boolean).join(" · ");

    row.replaceChildren(when, main, el("div", "todo-points", pointsLabel(item.points)));

    const listItem = el("li");
    listItem.append(row);
    currentList.append(listItem);
  }
}

// ---------- view switching ----------

function hideTooltip() {
  document.getElementById("tooltip").hidden = true;
  tooltipAnchor = null;
}

function render() {
  const now = new Date();
  hideTooltip(); // its anchor element is about to be replaced
  for (const button of document.querySelectorAll(".toggle")) {
    button.setAttribute("aria-pressed", String(button.dataset.view === state.view));
  }
  document.getElementById("calendar-view").hidden = state.view !== "calendar";
  document.getElementById("todo-view").hidden = state.view !== "todo";
  if (state.view === "calendar") renderCalendar(now);
  else renderTodo(now);
}

function setView(view) {
  state.view = view === "todo" ? "todo" : "calendar";
  if (location.hash !== "#" + state.view) history.replaceState(null, "", "#" + state.view);
  try { localStorage.setItem("tracker-view", state.view); } catch (e) { /* storage blocked: fine */ }
  render();
}

// ---------- data ----------

function statusSummary() {
  const now = new Date();
  const overdue = state.assignments.filter((item) => new Date(item.dueAt) < now).length;
  const weekEnd = addDays(startOfDay(now), 7);
  const thisWeek = state.assignments.filter((item) => {
    const due = new Date(item.dueAt);
    return due >= now && due < weekEnd;
  }).length;
  const updated = state.fetchedAt ? timeFmt.format(new Date(state.fetchedAt)) : "?";
  const parts = [`✓  ${state.assignments.length} incomplete`, `${thisWeek} due in the next 7 days`];
  if (overdue) parts.push(`⚠ ${overdue} overdue`);
  parts.push(`updated ${updated}`);
  if (state.demo) parts.push("demo data");
  else if (state.mode === "userscript") parts.push("browser sync");
  return parts.join(" · ");
}

async function loadAssignments(forceRefresh = false) {
  const refreshButton = document.getElementById("refresh");
  refreshButton.classList.add("busy");
  setStatus("Loading...");
  try {
    const response = await fetch("/api/assignments" + (forceRefresh ? "?refresh=1" : ""));
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `Server returned ${response.status}`);
    state.assignments = payload.assignments;
    state.fetchedAt = payload.fetchedAt;
    state.demo = payload.demo;
    state.mode = payload.mode || "canvas";
    state.error = null;
    render();
    setStatus(statusSummary());
  } catch (error) {
    state.error = error.message;
    // Keep showing the last good data, if any, and report the failure below it.
    setStatus(`⚠  ${error.message}`);
  } finally {
    refreshButton.classList.remove("busy");
  }
}

// ---------- tooltip (name on line 1, description after; 6px under the widget's centre) ----------

function setupTooltip() {
  const tooltip = document.getElementById("tooltip");

  document.addEventListener("mouseover", (event) => {
    const target = event.target.closest("[data-tip]");
    if (target === tooltipAnchor) return;
    tooltipAnchor = target;
    if (!target) { tooltip.hidden = true; return; }
    tooltip.textContent = target.dataset.tip;
    tooltip.hidden = false;
    const box = target.getBoundingClientRect();
    const tipBox = tooltip.getBoundingClientRect();
    let left = box.left + box.width / 2 - tipBox.width / 2;
    left = Math.max(6, Math.min(left, window.innerWidth - tipBox.width - 6));
    let top = box.bottom + 6;
    if (top + tipBox.height > window.innerHeight - 6) top = box.top - tipBox.height - 6;
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  });
  document.addEventListener("scroll", hideTooltip, true);
}

// ---------- start ----------

function init() {
  let initialView = location.hash.slice(1);
  if (!initialView) {
    try { initialView = localStorage.getItem("tracker-view") || ""; } catch (e) { initialView = ""; }
  }
  for (const button of document.querySelectorAll(".toggle")) {
    button.addEventListener("click", () => setView(button.dataset.view));
  }
  document.getElementById("refresh").addEventListener("click", () => loadAssignments(true));
  window.addEventListener("hashchange", () => setView(location.hash.slice(1)));
  setupTooltip();
  setView(initialView);

  loadAssignments();
  setInterval(() => loadAssignments(), refetchMs);
  setInterval(() => {
    render();
    if (!state.error && state.fetchedAt) setStatus(statusSummary());
  }, rerenderMs);
}

init();
