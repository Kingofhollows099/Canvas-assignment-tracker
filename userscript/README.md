# Browser-sync mode (no API token)

If your school has disabled Canvas personal access tokens, the server can't call
Canvas itself. This userscript fills the gap: it runs in **your** browser, reads
the assignment data Canvas already loaded onto your **Grades** page, and pushes it
to your local tracker. Anything you've submitted drops off the list automatically.

## Why this is fine

- It runs only on grades pages **you open yourself**, and only while you're there.
- It reads `window.ENV` — the data Canvas already sent your browser to draw your
  grades, which you're allowed to see. It does **not** call the Canvas API, use an
  access token, or copy your login.
- It sends only assignment names, due dates, point values and submitted/not-submitted
  to `http://localhost` on your own machine. Nothing leaves your computer.

This is deliberately different from pointing the server at your login credentials:
nothing is stored or automated behind your back, and it uses no access your browser
session doesn't already have while you're reading the page.

## Setup

1. Start the tracker in browser-sync mode:
   ```bash
   python server.py --source userscript
   ```
   (With no `CANVAS_API_TOKEN` set, plain `python server.py` picks this mode anyway.)

2. Install [Violentmonkey](https://violentmonkey.github.io/) (you already have it),
   then add `userscript/canvas-tracker-sync.user.js`:
   open the file's raw URL, or in the Violentmonkey dashboard choose
   **+ → Install from file** / paste the contents into a new script.

3. If your tracker runs on a port other than 8000, edit `trackerBase` at the top
   of the script.

## Using it

- Open any course's **Grades** page in Canvas (`.../courses/<id>/grades`).
- A raspberry **Sync assignments** pill appears bottom-right and syncs automatically;
  click it to sync again. It shows how many it sent and how many are already done.
- Visit each course's Grades page once to load everything; revisit after you turn
  work in and that assignment disappears from the tracker.
- Refresh the tracker tab (or press ↻) to see the update.

## Limits

- The tracker only knows about assignments from grades pages you've actually opened.
- It relies on Canvas's page data (`window.ENV`). If a future Canvas change moves
  that data, the pill will say it couldn't find assignments — tell me and I'll
  adjust the selectors.
