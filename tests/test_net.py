"""TLS trust handling for machines behind intercepting antivirus or proxies."""

import socket
import ssl

import pytest

from ytdl.core import net


@pytest.fixture(autouse=True)
def reset_injection_flag():
    original = net._injected
    yield
    net._injected = original


class TestInterceptorDetection:
    def test_identifies_avast_from_environment(self, monkeypatch):
        monkeypatch.setenv("SSLKEYLOGFILE", r"\\.\aswMonFltProxy\deadbeef")
        monkeypatch.setenv(
            "NODE_EXTRA_CA_CERTS", r"C:\ProgramData\Avast Software\Avast\wscert.pem"
        )
        assert net._detect_interceptor() == "Avast Web Shield"

    @pytest.mark.parametrize(
        "value,expected",
        [
            (r"C:\Program Files\Kaspersky Lab\cert.pem", "Kaspersky Web Anti-Virus"),
            ("/opt/zscaler/ca.pem", "Zscaler proxy"),
            ("/etc/eset/ca.pem", "ESET SSL/TLS filtering"),
        ],
    )
    def test_identifies_other_products(self, monkeypatch, value, expected):
        monkeypatch.delenv("SSLKEYLOGFILE", raising=False)
        monkeypatch.delenv("NODE_EXTRA_CA_CERTS", raising=False)
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", value)
        assert net._detect_interceptor() == expected

    def test_returns_none_on_a_clean_machine(self, monkeypatch):
        for var in ("SSLKEYLOGFILE", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
            monkeypatch.delenv(var, raising=False)
        assert net._detect_interceptor() is None


class TestDiagnose:
    def _fail_with(self, monkeypatch, message):
        def boom(*args, **kwargs):
            raise ssl.SSLCertVerificationError(message)

        monkeypatch.setattr(socket, "create_connection", boom)

    def test_malformed_ca_is_explained_and_attributed(self, monkeypatch):
        monkeypatch.setenv("NODE_EXTRA_CA_CERTS", r"C:\ProgramData\Avast Software\Avast\wscert.pem")
        self._fail_with(
            monkeypatch,
            "certificate verify failed: Basic Constraints of CA cert not marked critical",
        )

        result = net.diagnose()
        assert result.ok is False
        assert result.interceptor == "Avast Web Shield"
        assert "RFC 5280" in result.detail
        assert "truststore" in (result.remedy or "")

    def test_malformed_ca_without_a_known_product_still_advises(self, monkeypatch):
        for var in ("SSLKEYLOGFILE", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
            monkeypatch.delenv(var, raising=False)
        self._fail_with(monkeypatch, "certificate verify failed: Basic Constraints of CA cert not marked critical")

        result = net.diagnose()
        assert result.ok is False
        assert result.interceptor is None
        assert "truststore" in (result.remedy or "")

    def test_other_verification_failures_get_different_advice(self, monkeypatch):
        for var in ("SSLKEYLOGFILE", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
            monkeypatch.delenv(var, raising=False)
        self._fail_with(monkeypatch, "certificate verify failed: certificate has expired")

        result = net.diagnose()
        assert result.ok is False
        assert "clock" in (result.remedy or "")

    def test_unreachable_host_is_reported_as_connectivity(self, monkeypatch):
        def boom(*args, **kwargs):
            raise OSError("network unreachable")

        monkeypatch.setattr(socket, "create_connection", boom)
        result = net.diagnose()
        assert result.ok is False
        assert "network connection" in (result.remedy or "")

    def test_success_path(self, monkeypatch):
        class FakeSocket:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class FakeContext:
            def wrap_socket(self, sock, server_hostname=None): return FakeSocket()

        monkeypatch.setattr(socket, "create_connection", lambda *a, **k: FakeSocket())
        monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: FakeContext())

        result = net.diagnose()
        assert result.ok is True
        assert result.detail


class TestEnableSystemTrust:
    def test_reports_false_when_truststore_is_absent(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name == "truststore":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked)
        net._injected = False
        assert net.enable_system_trust() is False

    def test_is_idempotent_once_injected(self, monkeypatch):
        calls = []

        class FakeTruststore:
            @staticmethod
            def inject_into_ssl():
                calls.append(1)

        monkeypatch.setitem(__import__("sys").modules, "truststore", FakeTruststore)
        net._injected = False

        assert net.enable_system_trust() is True
        assert net.enable_system_trust() is True
        assert len(calls) == 1  # injected exactly once

    def test_diagnosis_is_json_ready(self):
        import json
        json.dumps(net.TlsDiagnosis(ok=True, using_system_trust=True, detail="x").as_dict())
