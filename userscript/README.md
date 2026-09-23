# Browser-sync mode (no API token)

If your school has disabled Canvas personal access tokens, the server can't call
Canvas itself. This userscript fills the gap: it runs in **your** browser, reads
the assignment data Canvas already loaded onto pages you open, and pushes it to
your local tracker. Anything you've submitted drops off the list automatically.

## What it syncs, and when

- **Grades page** (`.../courses/<id>/grades`) — syncs every assignment in that
  course at once, with due dates, points, and submitted status, read from Canvas'
  own page data (`window.ENV`).
- **Individual assignment page** (`.../courses/<id>/assignments/<id>`) — syncs that
  one assignment, and **hooks the Submit button**: the moment you turn it in, the
  tracker is updated (the page reload then confirms it).

Every item carries both the course id and the course name, and the server
backfills names across pages, so each assignment stays firmly tied to its class.

## Why this is acceptable

- It runs only on pages **you open yourself**, and only while you're there.
- It reads `window.ENV` and the page's own contents — data Canvas already sent your
  browser to draw pages you're allowed to see. It does **not** call the Canvas API,
  use an access token, or copy your login.
- It sends only assignment names, courses, due dates, points and submitted/not to
  `https://localhost` on your own machine. Nothing leaves your computer.

This is deliberately different from pointing the server at your login credentials:
nothing is stored or automated behind your back, and it uses no access your browser
session doesn't already have while you're reading the page.

## Encryption

All traffic between the userscript and the server is **encrypted with TLS** and
**signed with a shared token**, so nothing else on your computer can read or forge
these pushes:

- The server generates a self-signed certificate for `localhost` (in `certs/`,
  git-ignored) and serves over `https`.
- It also generates a random token (in `.sync_token`, git-ignored) that the
  userscript must send. Requests without it get 401.

## Setup

1. Start the tracker in browser-sync mode:
   ```bash
   python server.py --source userscript
   ```
   (With no `CANVAS_API_TOKEN` set, plain `python server.py` picks this mode anyway.)
   It prints the `https://localhost:<port>` address and your sync token.

2. **Trust the certificate once:** open that `https://localhost:<port>` address in
   the browser you use for Canvas and accept the self-signed certificate
   (Firefox: *Advanced → Accept the Risk*; Chrome: type `thisisunsafe` on the
   warning page). Until you do, the browser blocks the encrypted connection and the
   userscript's pushes will say "cert not trusted". Firefox tends to be the least
   fussy about a trusted localhost exception.

3. Install [Violentmonkey](https://violentmonkey.github.io/), then add
   `userscript/canvas-tracker-sync.user.js` (Violentmonkey dashboard →
   **+ → Install from file**, or paste its contents into a new script).

4. Edit the two lines at the top of the script to match what the server printed:
   ```js
   const trackerBase = "https://localhost:8000"; // same scheme + port
   const syncToken   = "the token the server printed";
   ```

## Using it

- Open any course's **Grades** page, or any **assignment** page, in Canvas.
- A raspberry pill appears bottom-right and syncs automatically; click it to sync
  again. It shows how many it sent and how many are already done.
- **Drag the pill** anywhere you like — its position is remembered across pages and
  reloads. To lock it to fixed coordinates instead, set `pillFixedPos` at the top of
  the script.
- The pill **fades out and removes itself after 5 seconds** so it never covers a
  Canvas button; it reappears the next time it syncs or has a status to show.
- Submit an assignment and it updates the tracker right away.
- Refresh the tracker tab (or press ↻) to see the change.

## Limits

- The tracker only knows about assignments from pages you've actually opened. Visit
  each course's Grades page once to load everything.
- It relies on Canvas's page data. If a future Canvas change moves that data, the
  pill will say it couldn't find assignments on the page — tell me and I'll adjust
  the selectors. (The Grades page uses the most stable source; assignment pages fall
  back to reading the visible page, which varies more by theme.)
