"""Canvas Assignment Tracker: a small web server with a calendar and a to-do view.

Run:  python server.py            (reads CANVAS_BASE_URL / CANVAS_API_TOKEN)
      python server.py --demo     (sample data, no Canvas account needed)
"""

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import canvas_client

appDir = Path(__file__).resolve().parent
staticDir = appDir / "static"

# Only these files are served, so no request path can reach anything else on disk.
staticFiles = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def loadDotEnv(envPath):
    """Load KEY=VALUE lines from a .env file without overriding real env vars."""
    if not envPath.is_file():
        return
    for rawLine in envPath.read_text(encoding="utf-8").splitlines():
        envLine = rawLine.strip()
        if not envLine or envLine.startswith("#") or "=" not in envLine:
            continue
        envKey, envValue = envLine.split("=", 1)
        envKey = envKey.strip().removeprefix("export ").strip()
        envValue = envValue.strip().strip('"').strip("'")
        os.environ.setdefault(envKey, envValue)


class AssignmentCache:
    """Keeps the last Canvas response for a few minutes so page loads stay fast
    and we don't hit Canvas' rate limits on every refresh."""

    def __init__(self, fetchFunction, ttlSeconds):
        self.fetchFunction = fetchFunction
        self.ttlSeconds = ttlSeconds
        self.cachedPayload = None
        self.cachedAt = 0.0
        self.cacheLock = threading.Lock()

    def get(self, forceRefresh=False):
        with self.cacheLock:
            isFresh = self.cachedPayload is not None and time.monotonic() - self.cachedAt < self.ttlSeconds
            if forceRefresh or not isFresh:
                assignmentList = self.fetchFunction()
                self.cachedPayload = {
                    "assignments": assignmentList,
                    "fetchedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                self.cachedAt = time.monotonic()
            return self.cachedPayload


class TrackerHandler(BaseHTTPRequestHandler):
    assignmentCache = None
    isDemo = False

    def do_GET(self):
        parsedUrl = urlparse(self.path)
        if parsedUrl.path == "/api/assignments":
            self.serveAssignments(parse_qs(parsedUrl.query))
        elif parsedUrl.path in staticFiles:
            fileName, contentType = staticFiles[parsedUrl.path]
            self.sendBytes(HTTPStatus.OK, (staticDir / fileName).read_bytes(), contentType)
        else:
            self.sendJson(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def serveAssignments(self, queryParams):
        forceRefresh = queryParams.get("refresh", ["0"])[0] == "1"
        try:
            responsePayload = dict(self.assignmentCache.get(forceRefresh))
        except canvas_client.CanvasError as canvasError:
            self.sendJson(canvasError.statusCode, {"error": str(canvasError)})
            return
        responsePayload["demo"] = self.isDemo
        self.sendJson(HTTPStatus.OK, responsePayload)

    def sendJson(self, statusCode, payload):
        self.sendBytes(statusCode, json.dumps(payload).encode("utf-8"), "application/json")

    def sendBytes(self, statusCode, bodyBytes, contentType):
        self.send_response(statusCode)
        self.send_header("Content-Type", contentType)
        self.send_header("Content-Length", str(len(bodyBytes)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(bodyBytes)

    def log_message(self, format, *args):
        # Quieter than the default: skip successful static/API hits.
        if args and str(args[1]).startswith(("2", "3")):
            return
        super().log_message(format, *args)


def main():
    loadDotEnv(appDir / ".env")

    argParser = argparse.ArgumentParser(description="Canvas assignment tracker web server")
    argParser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"),
                           help="Interface to bind (default 127.0.0.1: only this computer)")
    argParser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    argParser.add_argument("--demo", action="store_true", help="Serve sample data instead of calling Canvas")
    parsedArgs = argParser.parse_args()

    isDemo = parsedArgs.demo or os.environ.get("CANVAS_DEMO") == "1"
    if isDemo:
        fetchFunction = canvas_client.makeDemoAssignments
    else:
        baseUrl = os.environ.get("CANVAS_BASE_URL", "").strip().rstrip("/")
        apiToken = os.environ.get("CANVAS_API_TOKEN", "").strip()
        if not baseUrl or not apiToken:
            sys.exit("Set CANVAS_BASE_URL and CANVAS_API_TOKEN (in the environment or a .env file), "
                     "or run with --demo. See README.md.")
        if not baseUrl.startswith("https://"):
            sys.exit("CANVAS_BASE_URL must start with https:// so your token isn't sent in the clear.")
        lookbackDays = int(os.environ.get("CANVAS_LOOKBACK_DAYS", "14"))
        horizonDays = int(os.environ.get("CANVAS_HORIZON_DAYS", "60"))

        def fetchFunction():
            return canvas_client.getIncompleteAssignments(baseUrl, apiToken, lookbackDays, horizonDays)

    TrackerHandler.assignmentCache = AssignmentCache(fetchFunction, int(os.environ.get("CACHE_SECONDS", "300")))
    TrackerHandler.isDemo = isDemo

    httpServer = ThreadingHTTPServer((parsedArgs.host, parsedArgs.port), TrackerHandler)
    displayHost = "localhost" if parsedArgs.host in ("127.0.0.1", "0.0.0.0") else parsedArgs.host
    print(f"Canvas Assignment Tracker{' (demo data)' if isDemo else ''} on http://{displayHost}:{parsedArgs.port}")
    try:
        httpServer.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
