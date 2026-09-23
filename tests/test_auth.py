import unittest

import auth


class PasswordHashTests(unittest.TestCase):
    def test_hash_then_verify_roundtrip(self):
        stored = auth.hashPassword("correct horse battery staple")
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertTrue(auth.verifyPassword("correct horse battery staple", stored))

    def test_wrong_password_fails(self):
        stored = auth.hashPassword("hunter2")
        self.assertFalse(auth.verifyPassword("hunter3", stored))

    def test_salt_makes_hashes_differ(self):
        self.assertNotEqual(auth.hashPassword("same"), auth.hashPassword("same"))

    def test_malformed_stored_hash_is_rejected(self):
        for bad in ("", "notscrypt", "scrypt$only", "bcrypt$aa$bb"):
            self.assertFalse(auth.verifyPassword("x", bad))


class ThrottleTests(unittest.TestCase):
    def test_blocks_after_max_attempts(self):
        throttle = auth.LoginThrottle(maxAttempts=3, windowSeconds=300)
        for _ in range(3):
            self.assertFalse(throttle.isBlocked("1.2.3.4", now=100))
            throttle.recordFailure("1.2.3.4", now=100)
        self.assertTrue(throttle.isBlocked("1.2.3.4", now=100))

    def test_window_expires_old_attempts(self):
        throttle = auth.LoginThrottle(maxAttempts=2, windowSeconds=60)
        throttle.recordFailure("ip", now=0)
        throttle.recordFailure("ip", now=0)
        self.assertTrue(throttle.isBlocked("ip", now=30))
        self.assertFalse(throttle.isBlocked("ip", now=120))  # old ones aged out

    def test_reset_clears_attempts(self):
        throttle = auth.LoginThrottle(maxAttempts=1, windowSeconds=60)
        throttle.recordFailure("ip", now=0)
        throttle.reset("ip")
        self.assertFalse(throttle.isBlocked("ip", now=0))


class SessionTests(unittest.TestCase):
    def test_create_validate_destroy(self):
        manager = auth.SessionManager(ttlSeconds=1000)
        token = manager.create(now=0)
        self.assertTrue(manager.isValid(token, now=500))
        manager.destroy(token)
        self.assertFalse(manager.isValid(token, now=500))

    def test_expired_session_is_invalid(self):
        manager = auth.SessionManager(ttlSeconds=100)
        token = manager.create(now=0)
        self.assertFalse(manager.isValid(token, now=200))

    def test_unknown_and_empty_tokens_invalid(self):
        manager = auth.SessionManager()
        self.assertFalse(manager.isValid("nope"))
        self.assertFalse(manager.isValid(""))


if __name__ == "__main__":
    unittest.main()
