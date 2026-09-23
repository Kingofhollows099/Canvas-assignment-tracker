# Canvas Assignment Tracker

A small local web app that pulls your **incomplete** Canvas assignments and shows them two ways:

- **Calendar** – the next 7 days, today always in the leftmost column, each assignment listed at its due time.
- **To-do** – everything outstanding in one list, soonest due first, grouped into Overdue / Today / Tomorrow / this week / later.

It uses the Clipboard Cleaner design system (dark, flat, one raspberry accent), with 4px corners.

## Run it

Needs Python 3.9+ and nothing else — no packages to install.

```bash
cp .env.example .env      # then edit .env: set CANVAS_BASE_URL and CANVAS_API_TOKEN
python server.py
```

Open http://localhost:8000.

Want to look around first? `python server.py --demo` serves sample data without touching Canvas.

Options: `--port 8080`, `--host 0.0.0.0` (or the `PORT` / `HOST` variables in `.env`).

### No API token? Use browser-sync mode

If your school has disabled personal access tokens, run the tracker in **browser-sync**
mode instead. A small Violentmonkey userscript reads the assignment data Canvas already
loads onto your **Grades pages and individual assignment pages** and pushes it to the
tracker; submitting an assignment updates it immediately, and submitted work drops off
the list on its own. It uses no Canvas token and no login.

Traffic between the userscript and the server is encrypted with TLS and signed with a
shared token, both generated automatically on first run (kept in `certs/` and
`.sync_token`, git-ignored). Full walkthrough: [`userscript/README.md`](userscript/README.md).

```bash
python server.py --source userscript   # prints its https address and your sync token
```

### Getting a Canvas token

In Canvas: **Account → Settings → Approved Integrations → + New Access Token**. Copy the token into `.env`.
Treat it like a password — it can do anything your Canvas account can. `.env` is git-ignored so it won't be committed.

## How it works

- `server.py` – a standard-library HTTP server. Serves the page from `static/` and a JSON API:
  `GET /api/assignments` (`?refresh=1` skips the cache) and, in browser-sync mode, `POST /api/sync`
  for the userscript to push assignments to.
- `push_store.py` – validates and stores the assignments pushed from the browser, keyed by
  Canvas assignment id, with each item's course id and name (browser-sync mode).
- `tls_setup.py` – generates the self-signed localhost certificate and the shared sync token
  that encrypt and authenticate browser-sync traffic.
- `userscript/` – the Violentmonkey userscript and its setup guide for browser-sync mode.
- `canvas_client.py` – calls Canvas' Planner API (`/api/v1/planner/items?filter=incomplete_items`), which covers
  every course in a single paginated request. It then drops anything that isn't graded work (calendar events,
  notes, pages), anything submitted, excused or graded, and anything you've marked done in the Canvas planner.
- Responses are cached for 5 minutes so reloading the page doesn't hammer Canvas' rate limits; the ↻ button forces a
  fresh fetch. The page also refetches every 15 minutes and re-renders every minute, so "in 3h" labels stay current and
  the calendar rolls over at midnight.
- Dates travel as UTC and are converted to your browser's local time zone for display.

### Security notes

- The server binds to `127.0.0.1` by default, so only your own computer can open it. Your token never reaches the
  browser; only the server talks to Canvas.
- `CANVAS_BASE_URL` must be `https://`, and the client refuses to follow a pagination link to any other host, so the
  token is only ever sent to your Canvas instance.
- In browser-sync mode the server serves over HTTPS (self-signed localhost certificate) and requires a shared bearer
  token on `/api/sync`, so traffic to and from the userscript is encrypted and only your userscript can post. The
  `/api/sync` CORS allowance is scoped to `*.instructure.com` origins.

## Tests

```bash
python -m unittest -v
```
