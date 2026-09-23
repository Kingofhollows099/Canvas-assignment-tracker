"""Canvas Assignment Tracker: a small web server with a calendar and a to-do view.

Run:  python server.py                    (needs CANVAS_BASE_URL / CANVAS_API_TOKEN)
      python server.py --source userscript (assignments pushed in from the browser)
      python server.py --demo              (sample data, no Canvas account needed)
      python server.py --hash-password     (make a password hash for APP_PASSWORD_HASH)
"""

import argparse
import getpass
import hmac
import http.cookies
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

import auth
import canvas_client
import tls_setup
from push_store import PushStore

appDir = Path(__file__).resolve().parent
staticDir = appDir / "static"

# Requests over this many bytes are refused before we read the body.
maxSyncBodyBytes = 4 * 1024 * 1024
maxLoginBodyBytes = 16 * 1024

# Static files served to a logged-in browser. The login page is handled separately
# because it must be reachable without a session.
staticFiles = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
loopbackHosts = {"127.0.0.1", "::1", "localhost", ""}


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
    """Keeps the last assignment list for a few minutes so page loads stay fast
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

    def invalidate(self):
        with self.cacheLock:
            self.cachedPayload = None


def isAllowedSyncOrigin(originHeader, allowedOrigin):
    """True when a browser Origin may POST to /api/sync (used only for the CORS fallback)."""
    if not originHeader:
        return False
    if allowedOrigin and originHeader == allowedOrigin:
        return True
    # Default: any Canvas-hosted page. The userscript's own path (GM_xmlhttpRequest)
    # doesn't rely on this at all; it only matters for a plain-fetch fallback.
    parsedOrigin = urlparse(originHeader)
    return parsedOrigin.scheme == "https" and (
        parsedOrigin.hostname == "instructure.com"
        or (parsedOrigin.hostname or "").endswith(".instructure.com")
    )


class TrackerHandler(BaseHTTPRequestHandler):
    assignmentCache = None
    pushStore = None          # set only in userscript mode
    mode = "canvas"
    allowedOrigin = ""
    syncToken = ""            # shared bearer token required to POST /api/sync

    # Web-UI login
    authEnabled = False
    authUsername = ""
    authPasswordHash = ""
    sessionManager = None
    loginThrottle = None
    cookieSecure = True

    # ---- reads ----

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/login":
            if self.hasValidSession():
                self.redirect("/")
            else:
                self.sendBytes(HTTPStatus.OK, (staticDir / "login.html").read_bytes(), "text/html; charset=utf-8")
            return

        if path == "/api/assignments":
            if not self.requireSessionForApi():
                return
            self.serveAssignments(parse_qs(urlparse(self.path).query))
            return

        if path in staticFiles:
            if not self.requireSessionForPage():
                return
            fileName, contentType = staticFiles[path]
            self.sendBytes(HTTPStatus.OK, (staticDir / fileName).read_bytes(), contentType)
            return

        self.sendJson(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def serveAssignments(self, queryParams):
        forceRefresh = queryParams.get("refresh", ["0"])[0] == "1"
        try:
            responsePayload = dict(self.assignmentCache.get(forceRefresh))
        except canvas_client.CanvasError as canvasError:
            self.sendJson(canvasError.statusCode, {"error": str(canvasError)})
            return
        responsePayload["demo"] = self.mode == "demo"
        responsePayload["mode"] = self.mode
        responsePayload["authEnabled"] = self.authEnabled
        self.sendJson(HTTPStatus.OK, responsePayload)

    # ---- writes ----

    def do_OPTIONS(self):
        # CORS preflight for the plain-fetch fallback path.
        if urlparse(self.path).path == "/api/sync":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.applyCorsHeaders()
            self.end_headers()
        else:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/login":
            self.handleLogin()
        elif path == "/logout":
            self.handleLogout()
        elif path == "/api/sync":
            self.handleSync()
        else:
            self.sendJson(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def handleSync(self):
        if self.pushStore is None:
            self.sendJson(HTTPStatus.CONFLICT,
                          {"error": "Server is not in userscript mode; start it with --source userscript."})
            return
        if not self.isAuthorizedSync():
            self.sendJson(HTTPStatus.UNAUTHORIZED, {"error": "Missing or wrong sync token."})
            return

        requestBody = self.readJsonBody(maxSyncBodyBytes)
        if requestBody is None:
            return
        try:
            accepted, skipped = self.pushStore.sync(requestBody.get("assignments"))
        except (AttributeError, ValueError) as syncError:
            self.sendJson(HTTPStatus.BAD_REQUEST, {"error": str(syncError) or "Bad request body."})
            return

        self.assignmentCache.invalidate()  # next page load reflects the sync immediately
        self.sendJson(HTTPStatus.OK, {"accepted": accepted, "skipped": skipped})

    def handleLogin(self):
        if not self.authEnabled:
            self.redirect("/")
            return
        clientKey = self.clientKey()
        if self.loginThrottle.isBlocked(clientKey):
            self.redirect("/login?error=throttled")
            return

        formFields = self.readFormBody(maxLoginBodyBytes)
        username = (formFields.get("username", [""])[0]).strip()
        password = formFields.get("password", [""])[0]

        usernameOk = hmac.compare_digest(username, self.authUsername)
        passwordOk = auth.verifyPassword(password, self.authPasswordHash)
        if not (usernameOk and passwordOk):
            self.loginThrottle.recordFailure(clientKey)
            self.redirect("/login?error=1")
            return

        self.loginThrottle.reset(clientKey)
        token = self.sessionManager.create()
        self.redirect("/", setCookie=self.buildSessionCookie(token))

    def handleLogout(self):
        cookies = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        token = cookies[auth.sessionCookieName].value if auth.sessionCookieName in cookies else ""
        if self.sessionManager:
            self.sessionManager.destroy(token)
        self.redirect("/login", setCookie=self.buildSessionCookie("", expire=True))

    # ---- auth helpers ----

    def hasValidSession(self):
        if not self.authEnabled:
            return True
        cookies = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
        if auth.sessionCookieName not in cookies:
            return False
        return self.sessionManager.isValid(cookies[auth.sessionCookieName].value)

    def requireSessionForPage(self):
        if self.hasValidSession():
            return True
        self.redirect("/login")
        return False

    def requireSessionForApi(self):
        if self.hasValidSession():
            return True
        self.sendJson(HTTPStatus.UNAUTHORIZED, {"error": "Not signed in."})
        return False

    def isAuthorizedSync(self):
        """Constant-time check of the Authorization: Bearer <token> header."""
        if not self.syncToken:
            return False
        authHeader = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not authHeader.startswith(prefix):
            return False
        return hmac.compare_digest(authHeader[len(prefix):], self.syncToken)

    def clientKey(self):
        # Behind a reverse proxy the real client is in X-Forwarded-For's first hop.
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return self.client_address[0] if self.client_address else "unknown"

    def buildSessionCookie(self, token, expire=False):
        cookie = http.cookies.SimpleCookie()
        cookie[auth.sessionCookieName] = token
        morsel = cookie[auth.sessionCookieName]
        morsel["path"] = "/"
        morsel["httponly"] = True
        morsel["samesite"] = "Lax"
        if self.cookieSecure:
            morsel["secure"] = True
        morsel["max-age"] = 0 if expire else self.sessionManager.ttlSeconds
        return morsel.OutputString()

    # ---- request-body helpers ----

    def readJsonBody(self, limit):
        try:
            contentLength = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            contentLength = 0
        if contentLength <= 0 or contentLength > limit:
            self.sendJson(HTTPStatus.BAD_REQUEST, {"error": "Missing or oversized request body."})
            return None
        try:
            return json.loads(self.rfile.read(contentLength))
        except json.JSONDecodeError:
            self.sendJson(HTTPStatus.BAD_REQUEST, {"error": "Body must be valid JSON."})
            return None

    def readFormBody(self, limit):
        try:
            contentLength = min(int(self.headers.get("Content-Length", "0")), limit)
        except ValueError:
            contentLength = 0
        if contentLength <= 0:
            return {}
        rawBody = self.rfile.read(contentLength).decode("utf-8", errors="replace")
        return parse_qs(rawBody, keep_blank_values=True)

    # ---- response helpers ----

    def redirect(self, location, setCookie=None):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        if setCookie:
            self.send_header("Set-Cookie", setCookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def applyCorsHeaders(self):
        originHeader = self.headers.get("Origin")
        if isAllowedSyncOrigin(originHeader, self.allowedOrigin):
            self.send_header("Access-Control-Allow-Origin", originHeader)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Max-Age", "86400")

    def sendJson(self, statusCode, payload):
        self.sendBytes(statusCode, json.dumps(payload).encode("utf-8"), "application/json")

    def sendBytes(self, statusCode, bodyBytes, contentType):
        self.send_response(statusCode)
        self.send_header("Content-Type", contentType)
        self.send_header("Content-Length", str(len(bodyBytes)))
        self.send_header("Cache-Control", "no-store")
        if urlparse(self.path).path == "/api/sync":
            self.applyCorsHeaders()
        self.end_headers()
        self.wfile.write(bodyBytes)

    def log_message(self, format, *args):
        # Quieter than the default: skip successful static/API hits.
        if args and str(args[1]).startswith(("2", "3")):
            return
        super().log_message(format, *args)


def chooseMode(parsedArgs):
    """Resolve the effective data source from flags and environment."""
    requested = parsedArgs.source
    if parsedArgs.demo:
        requested = "demo"
    if requested == "auto":
        if os.environ.get("CANVAS_DEMO") == "1":
            return "demo"
        if os.environ.get("CANVAS_API_TOKEN", "").strip():
            return "canvas"
        # No token (e.g. the school disabled them): accept pushes from the browser.
        return "userscript"
    return requested


def buildCanvasFetch():
    baseUrl = os.environ.get("CANVAS_BASE_URL", "").strip().rstrip("/")
    apiToken = os.environ.get("CANVAS_API_TOKEN", "").strip()
    if not baseUrl or not apiToken:
        sys.exit("Canvas mode needs CANVAS_BASE_URL and CANVAS_API_TOKEN (in the environment or a .env "
                 "file). If your school disabled tokens, use --source userscript. See README.md.")
    if not baseUrl.startswith("https://"):
        sys.exit("CANVAS_BASE_URL must start with https:// so your token isn't sent in the clear.")
    lookbackDays = int(os.environ.get("CANVAS_LOOKBACK_DAYS", "14"))
    horizonDays = int(os.environ.get("CANVAS_HORIZON_DAYS", "60"))

    def fetchFunction():
        return canvas_client.getIncompleteAssignments(baseUrl, apiToken, lookbackDays, horizonDays)

    return fetchFunction


def resolvePasswordHash():
    """The stored password hash, from APP_PASSWORD_HASH or by hashing APP_PASSWORD."""
    passwordHash = os.environ.get("APP_PASSWORD_HASH", "").strip()
    if passwordHash:
        return passwordHash
    plainPassword = os.environ.get("APP_PASSWORD", "")
    if plainPassword:
        return auth.hashPassword(plainPassword)
    return ""


def configureAuth(parsedArgs):
    """Set up web-UI login and refuse to expose the app unauthenticated."""
    username = os.environ.get("APP_USERNAME", "").strip()
    passwordHash = resolvePasswordHash()
    authEnabled = bool(username and passwordHash)

    exposed = parsedArgs.host not in loopbackHosts
    if exposed and not authEnabled and not parsedArgs.allow_no_auth:
        sys.exit(
            "Refusing to bind to a public interface without a login.\n"
            "Set APP_USERNAME and APP_PASSWORD (or APP_PASSWORD_HASH) to require sign-in, "
            "or pass --allow-no-auth if you really mean to run it open (e.g. behind another "
            "auth layer)."
        )

    TrackerHandler.authEnabled = authEnabled
    TrackerHandler.authUsername = username
    TrackerHandler.authPasswordHash = passwordHash
    TrackerHandler.sessionManager = auth.SessionManager(int(os.environ.get("SESSION_TTL_SECONDS", str(7 * 24 * 3600))))
    TrackerHandler.loginThrottle = auth.LoginThrottle()
    # Cookies are marked Secure by default (the browser reaches us over HTTPS via the
    # reverse proxy). Set COOKIE_SECURE=0 only for plain-HTTP local testing.
    TrackerHandler.cookieSecure = os.environ.get("COOKIE_SECURE", "1") != "0"
    return authEnabled


def main():
    loadDotEnv(appDir / ".env")

    argParser = argparse.ArgumentParser(description="Canvas assignment tracker web server")
    argParser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"),
                           help="Interface to bind (default 127.0.0.1: only this computer)")
    argParser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    argParser.add_argument("--source", choices=["auto", "canvas", "userscript", "demo"],
                           default=os.environ.get("CANVAS_SOURCE", "auto"),
                           help="Where assignments come from (default auto)")
    argParser.add_argument("--demo", action="store_true", help="Shortcut for --source demo")
    argParser.add_argument("--tls", dest="tls", action="store_true", default=None,
                           help="Serve over HTTPS (default on in userscript mode)")
    argParser.add_argument("--no-tls", dest="tls", action="store_false",
                           help="Serve over plain HTTP (use behind a TLS-terminating proxy)")
    argParser.add_argument("--allow-no-auth", action="store_true",
                           help="Allow binding to a public interface without a login (not recommended)")
    argParser.add_argument("--hash-password", action="store_true",
                           help="Prompt for a password, print a hash for APP_PASSWORD_HASH, and exit")
    parsedArgs = argParser.parse_args()

    if parsedArgs.hash_password:
        entered = getpass.getpass("Password: ")
        if entered != getpass.getpass("Confirm:  "):
            sys.exit("Passwords did not match.")
        print(auth.hashPassword(entered))
        return

    mode = chooseMode(parsedArgs)
    lookbackDays = int(os.environ.get("CANVAS_LOOKBACK_DAYS", "14"))
    horizonDays = int(os.environ.get("CANVAS_HORIZON_DAYS", "60"))

    syncToken = ""
    if mode == "demo":
        fetchFunction = canvas_client.makeDemoAssignments
    elif mode == "userscript":
        storePath = Path(os.environ.get("PUSH_STORE_PATH", appDir / "pushed_assignments.json"))
        pushStore = PushStore(storePath, lookbackDays, max(horizonDays, 90))
        TrackerHandler.pushStore = pushStore
        fetchFunction = pushStore.getIncomplete
        syncToken = os.environ.get("SYNC_TOKEN", "").strip() or tls_setup.ensureSyncToken(appDir / ".sync_token")
    else:
        fetchFunction = buildCanvasFetch()

    TrackerHandler.assignmentCache = AssignmentCache(fetchFunction, int(os.environ.get("CACHE_SECONDS", "300")))
    TrackerHandler.mode = mode
    TrackerHandler.allowedOrigin = os.environ.get("CANVAS_BASE_URL", "").strip().rstrip("/")
    TrackerHandler.syncToken = syncToken
    authEnabled = configureAuth(parsedArgs)

    # TLS is on by default when the browser userscript talks to us directly, and off
    # when a reverse proxy terminates TLS (--no-tls).
    useTls = parsedArgs.tls if parsedArgs.tls is not None else (mode == "userscript")
    sslContext = None
    if useTls:
        try:
            certPath, keyPath = tls_setup.ensureCertificate(appDir / "certs" / "localhost.crt",
                                                            appDir / "certs" / "localhost.key")
            sslContext = tls_setup.buildServerSslContext(certPath, keyPath)
        except tls_setup.TlsSetupError as tlsError:
            sys.exit(f"Could not enable HTTPS: {tlsError}\nOr run with --no-tls to serve over plain HTTP.")

    httpServer = ThreadingHTTPServer((parsedArgs.host, parsedArgs.port), TrackerHandler)
    if sslContext is not None:
        httpServer.socket = sslContext.wrap_socket(httpServer.socket, server_side=True)

    scheme = "https" if sslContext is not None else "http"
    displayHost = "localhost" if parsedArgs.host in ("127.0.0.1", "0.0.0.0") else parsedArgs.host
    baseAddress = f"{scheme}://{displayHost}:{parsedArgs.port}"
    label = {"demo": " (demo data)", "userscript": " (browser sync)"}.get(mode, "")
    print(f"Canvas Assignment Tracker{label} on {baseAddress}")
    print(f"Web-UI login: {'on' if authEnabled else 'OFF (anyone who can reach this can view it)'}")
    if mode == "userscript":
        print("\nBrowser-sync setup (see userscript/README.md):")
        if scheme == "https":
            print(f"  1. Open {baseAddress} once and trust the self-signed certificate.")
        print(f"  2. In the userscript, set  trackerBase = \"{baseAddress}\"  (or your proxied URL)")
        print(f"     and  syncToken  = \"{syncToken}\"")
        print("  Then open a course's Grades or an assignment page in Canvas.\n")
    try:
        httpServer.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
