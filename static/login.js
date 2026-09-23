"use strict";
// Show messages passed back via the query string, without echoing anything user-supplied.
const params = new URLSearchParams(location.search);
const error = document.getElementById("error");
if (params.get("error") === "throttled") {
  error.textContent = "⚠  Too many attempts. Wait a few minutes and try again.";
  error.hidden = false;
} else if (params.has("error")) {
  error.hidden = false;
}
