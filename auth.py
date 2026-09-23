"""Username/password login for the tracker's web interface.

When the tracker is exposed to the internet (e.g. through Nginx Proxy Manager on a
home server), the calendar and the /api/assignments endpoint must not be open to
the world. This module provides:

  * password hashing with scrypt (standard library, no dependencies), so the
    stored/configured secret is never a plaintext password at rest if you use a hash;
  * server-side sessions with signed, HttpOnly cookies;
  * a small brute-force throttle on failed logins.

The userscript's /api/sync endpoint is NOT covered here — it authenticates with its
own bearer token, because a browser userscript can't perform a cookie login.
"""

import hashlib
import hmac
import secrets
import threading
import time

# scrypt cost parameters. These are deliberately expensive to slow guessing.
scryptN = 2 ** 14
scryptR = 8
scryptP = 1
scryptDkLen = 32
scryptMaxMem = 64 * 1024 * 1024

sessionCookieName = "cat_session"


def hashPassword(plainPassword, salt=None):
    """Return a 'scrypt$<saltHex>$<hashHex>' string for a password."""
    saltBytes = salt if salt is not None else secrets.token_bytes(16)
    derived = hashlib.scrypt(plainPassword.encode("utf-8"), salt=saltBytes,
                             n=scryptN, r=scryptR, p=scryptP, dklen=scryptDkLen, maxmem=scryptMaxMem)
    return f"scrypt${saltBytes.hex()}${derived.hex()}"


def verifyPassword(plainPassword, storedHash):
    """Constant-time check of a password against a stored 'scrypt$salt$hash' string."""
    try:
        scheme, saltHex, hashHex = storedHash.split("$", 2)
    except (ValueError, AttributeError):
        return False
    if scheme != "scrypt":
        return False
    try:
        candidate = hashlib.scrypt(plainPassword.encode("utf-8"), salt=bytes.fromhex(saltHex),
                                   n=scryptN, r=scryptR, p=scryptP, dklen=scryptDkLen, maxmem=scryptMaxMem)
    except ValueError:
        return False
    return hmac.compare_digest(candidate.hex(), hashHex)


class LoginThrottle:
    """Slows down repeated failed logins from the same client."""

    def __init__(self, maxAttempts=5, windowSeconds=300):
        self.maxAttempts = maxAttempts
        self.windowSeconds = windowSeconds
        self.attemptsByClient = {}
        self.throttleLock = threading.Lock()

    def isBlocked(self, clientKey, now=None):
        if now is None:
            now = time.monotonic()
        with self.throttleLock:
            attempts = [stamp for stamp in self.attemptsByClient.get(clientKey, []) if now - stamp < self.windowSeconds]
            self.attemptsByClient[clientKey] = attempts
            return len(attempts) >= self.maxAttempts

    def recordFailure(self, clientKey, now=None):
        if now is None:
            now = time.monotonic()
        with self.throttleLock:
            self.attemptsByClient.setdefault(clientKey, []).append(now)

    def reset(self, clientKey):
        with self.throttleLock:
            self.attemptsByClient.pop(clientKey, None)


class SessionManager:
    """In-memory login sessions keyed by a random cookie value."""

    def __init__(self, ttlSeconds=7 * 24 * 3600):
        self.ttlSeconds = ttlSeconds
        self.sessions = {}  # token -> expiry (monotonic seconds)
        self.sessionLock = threading.Lock()

    def create(self, now=None):
        if now is None:
            now = time.monotonic()
        token = secrets.token_urlsafe(32)
        with self.sessionLock:
            self.sessions[token] = now + self.ttlSeconds
        return token

    def isValid(self, token, now=None):
        if not token:
            return False
        if now is None:
            now = time.monotonic()
        with self.sessionLock:
            expiry = self.sessions.get(token)
            if expiry is None:
                return False
            if expiry < now:
                del self.sessions[token]
                return False
            return True

    def destroy(self, token):
        with self.sessionLock:
            self.sessions.pop(token, None)
