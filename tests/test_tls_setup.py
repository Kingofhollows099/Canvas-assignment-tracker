import tempfile
import unittest
from pathlib import Path

import tls_setup


class SyncTokenTests(unittest.TestCase):
    def test_creates_then_reuses_token(self):
        with tempfile.TemporaryDirectory() as tempDir:
            tokenPath = Path(tempDir) / ".sync_token"
            first = tls_setup.ensureSyncToken(tokenPath)
            self.assertTrue(len(first) >= 32)
            second = tls_setup.ensureSyncToken(tokenPath)
            self.assertEqual(first, second)  # stable across calls


class CertificateTests(unittest.TestCase):
    def test_generates_a_usable_certificate(self):
        with tempfile.TemporaryDirectory() as tempDir:
            certPath = Path(tempDir) / "certs" / "localhost.crt"
            keyPath = Path(tempDir) / "certs" / "localhost.key"
            tls_setup.ensureCertificate(certPath, keyPath)
            self.assertTrue(certPath.is_file() and keyPath.is_file())
            # Loads into a real TLS context without error.
            tls_setup.buildServerSslContext(certPath, keyPath)


if __name__ == "__main__":
    unittest.main()
