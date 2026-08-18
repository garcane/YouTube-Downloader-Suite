"""TLS trust configuration for machines behind intercepting proxies.

Antivirus products (Avast, Kaspersky, ESET, Bitdefender) and corporate proxies
terminate HTTPS locally and re-sign traffic with their own root certificate.
Some of those roots are not RFC 5280 conforming — Avast's Web/Mail Shield root,
for instance, carries ``basicConstraints`` without marking it critical, which
RFC 5280 §4.2.1.9 requires for a CA certificate.

OpenSSL 1.1.1 tolerated that. OpenSSL 3.x, which ships with Python 3.10+ on
Windows, rejects it outright::

    [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed:
    Basic Constraints of CA cert not marked critical

Crucially this is *not* a trust problem: adding the certificate to the bundle
does not help, because it is rejected as malformed before trust is evaluated.
The only clean fix is to stop using OpenSSL's verifier and use the operating
system's own — Windows SChannel, macOS Security.framework — which do not
enforce that particular rule. The ``truststore`` package does exactly that, and
pip itself has used it by default since 24.0 for the same reason.

This module makes that opt-in behaviour automatic when ``truststore`` is
installed, and degrades to a clear diagnostic when it is not. Certificate
verification is never disabled.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from typing import Optional

_injected = False


def enable_system_trust(*, force: bool = False) -> bool:
    """Route TLS verification through the OS certificate verifier.

    Idempotent and safe to call from every interface's startup path.

    :returns: ``True`` if the system verifier is now in use, ``False`` if
        ``truststore`` is not installed (in which case OpenSSL's verifier
        remains in place and nothing has changed).
    """
    global _injected

    if _injected and not force:
        return True

    try:
        import truststore
    except ImportError:
        return False

    truststore.inject_into_ssl()
    _injected = True
    return True


@dataclass(frozen=True)
class TlsDiagnosis:
    """Human-readable summary of why HTTPS is or is not working."""

    ok: bool
    using_system_trust: bool
    detail: str
    interceptor: Optional[str] = None
    remedy: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "using_system_trust": self.using_system_trust,
            "detail": self.detail,
            "interceptor": self.interceptor,
            "remedy": self.remedy,
        }


#: Signatures of common TLS-intercepting products, matched against the error
#: text and the local environment to turn an opaque SSL failure into advice.
_KNOWN_INTERCEPTORS = {
    "avast": "Avast Web Shield",
    "avg": "AVG Web Shield",
    "kaspersky": "Kaspersky Web Anti-Virus",
    "eset": "ESET SSL/TLS filtering",
    "bitdefender": "Bitdefender SSL Scan",
    "zscaler": "Zscaler proxy",
    "netskope": "Netskope proxy",
    "fortinet": "Fortinet SSL inspection",
}


def _detect_interceptor() -> Optional[str]:
    """Best-effort identification of a local TLS interceptor."""
    import os

    haystack = " ".join(
        [
            os.environ.get("SSLKEYLOGFILE", ""),
            os.environ.get("NODE_EXTRA_CA_CERTS", ""),
            os.environ.get("REQUESTS_CA_BUNDLE", ""),
            os.environ.get("SSL_CERT_FILE", ""),
        ]
    ).lower()

    for needle, label in _KNOWN_INTERCEPTORS.items():
        if needle in haystack:
            return label
    return None


def diagnose(host: str = "www.youtube.com", *, timeout: float = 10.0) -> TlsDiagnosis:
    """Check whether an HTTPS request to ``host`` can be verified.

    Used by the interfaces to explain a connection failure in terms the user can
    act on, instead of surfacing a raw ``SSLCertVerificationError``.
    """
    import socket

    interceptor = _detect_interceptor()

    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host):
                pass
    except ssl.SSLCertVerificationError as exc:
        reason = str(exc)
        malformed_ca = "Basic Constraints" in reason

        remedy = (
            "Install the system-trust bridge:  pip install truststore\n"
            "The application uses it automatically once present."
        )
        if malformed_ca and interceptor:
            detail = (
                f"HTTPS is being intercepted by {interceptor}, whose root certificate "
                f"is not RFC 5280 conforming, so OpenSSL refuses it."
            )
        elif malformed_ca:
            detail = (
                "HTTPS is being intercepted by software whose root certificate is not "
                "RFC 5280 conforming, so OpenSSL refuses it."
            )
        else:
            detail = f"Certificate verification failed: {reason}"
            remedy = (
                "Check the system clock, then try:  pip install -U certifi truststore"
            )

        return TlsDiagnosis(
            ok=False,
            using_system_trust=_injected,
            detail=detail,
            interceptor=interceptor,
            remedy=remedy,
        )
    except (OSError, socket.timeout) as exc:
        return TlsDiagnosis(
            ok=False,
            using_system_trust=_injected,
            detail=f"Could not reach {host}: {exc}",
            interceptor=interceptor,
            remedy="Check the network connection.",
        )

    return TlsDiagnosis(
        ok=True,
        using_system_trust=_injected,
        detail=(
            "HTTPS verified using the operating system certificate store."
            if _injected
            else "HTTPS verified using the bundled OpenSSL certificate store."
        ),
        interceptor=interceptor,
    )
