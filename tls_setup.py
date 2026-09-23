"""Sets up encryption for browser-sync mode.

All traffic between the browser userscript and this server is encrypted with TLS.
Because the server runs on your own machine, there's no public certificate
authority to issue a certificate for "localhost", so we generate a self-signed
one (via openssl) the first time and reuse it. A shared bearer token is also
generated so that only your userscript — not some other program on your computer —
can post to the sync endpoint.

Both the certificate and the token live in files that are git-ignored; neither
ever leaves your machine.
"""

import secrets
import ssl
import subprocess
from pathlib import Path


class TlsSetupError(Exception):
    """Raised when the certificate can't be created (e.g. openssl is missing)."""


def ensureSyncToken(tokenPath):
    """Return the shared bearer token, creating and saving one if needed."""
    tokenPath = Path(tokenPath)
    if tokenPath.is_file():
        existingToken = tokenPath.read_text(encoding="utf-8").strip()
        if existingToken:
            return existingToken
    newToken = secrets.token_urlsafe(32)
    tokenPath.write_text(newToken + "\n", encoding="utf-8")
    try:
        tokenPath.chmod(0o600)  # readable only by you, where the OS supports it
    except OSError:
        pass
    return newToken


def ensureCertificate(certPath, keyPath):
    """Create a self-signed localhost certificate if it isn't already present."""
    certPath = Path(certPath)
    keyPath = Path(keyPath)
    if certPath.is_file() and keyPath.is_file():
        return certPath, keyPath

    certPath.parent.mkdir(parents=True, exist_ok=True)
    opensslCommand = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", str(keyPath), "-out", str(certPath),
        "-days", "3650", "-nodes", "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
    ]
    try:
        subprocess.run(opensslCommand, check=True, capture_output=True)
    except FileNotFoundError:
        raise TlsSetupError(
            "openssl isn't installed, so a TLS certificate can't be created. Install openssl, "
            "or generate certs/localhost.crt and certs/localhost.key yourself."
        )
    except subprocess.CalledProcessError as opensslError:
        raise TlsSetupError(f"openssl failed to create a certificate: {opensslError.stderr.decode(errors='replace')}")

    try:
        keyPath.chmod(0o600)
    except OSError:
        pass
    return certPath, keyPath


def buildServerSslContext(certPath, keyPath):
    """A TLS context that serves the self-signed certificate."""
    sslContext = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sslContext.load_cert_chain(certfile=str(certPath), keyfile=str(keyPath))
    return sslContext
