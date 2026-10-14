"""Use distro trust when a relocated Linux build has lost its compiled CA path.

Never override an explicit user CA, or weaken certificate/hostname checks.
Only the application process environment changes; system trust stays untouched.
"""
from __future__ import annotations

from pathlib import Path
import os
import ssl


SYSTEM_CA_FILES = (
    Path("/etc/ssl/certs/ca-certificates.crt"),
    Path("/etc/pki/tls/certs/ca-bundle.crt"),
    Path("/etc/ssl/cert.pem"),
)


def configure_tls() -> str | None:
    if "SSL_CERT_FILE" in os.environ or "SSL_CERT_DIR" in os.environ:
        return None
    defaults = ssl.get_default_verify_paths()
    if defaults.cafile or defaults.capath:
        return None
    for candidate in SYSTEM_CA_FILES:
        if not candidate.is_file():
            continue
        try:
            context = ssl.create_default_context(cafile=str(candidate))
        except (OSError, ssl.SSLError):
            continue
        if context.cert_store_stats()["x509_ca"]:
            # Initialization normally runs before workers; setdefault also
            # avoids replacing a CA file supplied by another initializer.
            if "SSL_CERT_DIR" not in os.environ:
                selected = os.environ.setdefault("SSL_CERT_FILE", str(candidate))
                return str(candidate) if selected == str(candidate) else None
    return None
