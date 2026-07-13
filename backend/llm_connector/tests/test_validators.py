"""SSRF URL validator for user-supplied LLM endpoints.

Loopback/private literals need no DNS; the hostname->internal cases patch socket.getaddrinfo so
the suite stays offline and deterministic. The operator SSRF policy (`LLM_URL_ALLOWLIST` /
`LLM_URL_ALLOW_PRIVATE`) is pinned per test with `override_settings` so results don't depend on
the ambient DEBUG-derived default in settings.py.
"""

from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from llm_connector.validators import validate_safe_llm_url


def _addrinfo(ip):
    # Shape of socket.getaddrinfo()[i] — only [4][0] (the IP) is read by the validator.
    return [(2, 1, 6, "", (ip, 0))]


@override_settings(LLM_URL_ALLOWLIST=[], LLM_URL_ALLOW_PRIVATE=False)
class ValidateSafeLLMUrlDenyByDefaultTests(TestCase):
    """No operator allowance: private / loopback destinations are refused."""

    def test_empty_is_allowed(self):
        validate_safe_llm_url("")  # url-less providers pass through

    def test_public_https_passes(self):
        with mock.patch("socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")):
            validate_safe_llm_url("https://api.example.com/v1")

    def test_rejects_non_http_scheme(self):
        with self.assertRaises(ValidationError):
            validate_safe_llm_url("file:///etc/passwd")
        with self.assertRaises(ValidationError):
            validate_safe_llm_url("gopher://example.com")

    def test_rejects_missing_host(self):
        with self.assertRaises(ValidationError):
            validate_safe_llm_url("http:///v1")

    def test_rejects_loopback_literal(self):
        with self.assertRaises(ValidationError):
            validate_safe_llm_url("http://127.0.0.1:11434/v1")

    def test_rejects_localhost_name(self):
        with mock.patch("socket.getaddrinfo", return_value=_addrinfo("127.0.0.1")):
            with self.assertRaises(ValidationError):
                validate_safe_llm_url("http://localhost:11434/v1")

    def test_rejects_private_range(self):
        with self.assertRaises(ValidationError):
            validate_safe_llm_url("http://10.0.0.5/v1")
        with self.assertRaises(ValidationError):
            validate_safe_llm_url("http://192.168.1.10/v1")

    def test_rejects_cloud_metadata(self):
        with self.assertRaises(ValidationError):
            validate_safe_llm_url("http://169.254.169.254/latest/meta-data/")


class ValidateSafeLLMUrlAllowlistTests(TestCase):
    """The operator vouches for specific internal destinations."""

    @override_settings(LLM_URL_ALLOWLIST=["localhost"], LLM_URL_ALLOW_PRIVATE=False)
    def test_allowlisted_hostname_passes(self):
        with mock.patch("socket.getaddrinfo", return_value=_addrinfo("127.0.0.1")):
            validate_safe_llm_url("http://localhost:11434/v1")

    @override_settings(LLM_URL_ALLOWLIST=["127.0.0.1"], LLM_URL_ALLOW_PRIVATE=False)
    def test_allowlisted_ip_literal_passes(self):
        validate_safe_llm_url("http://127.0.0.1:11434/v1")

    @override_settings(LLM_URL_ALLOWLIST=["10.8.0.0/24"], LLM_URL_ALLOW_PRIVATE=False)
    def test_allowlisted_cidr_passes(self):
        validate_safe_llm_url("http://10.8.0.7:11434/v1")  # VPN subnet member

    @override_settings(LLM_URL_ALLOWLIST=["10.8.0.0/24"], LLM_URL_ALLOW_PRIVATE=False)
    def test_private_ip_outside_cidr_still_rejected(self):
        with self.assertRaises(ValidationError):
            validate_safe_llm_url("http://10.9.0.7:11434/v1")

    @override_settings(LLM_URL_ALLOWLIST=["ollama.example.ts.net"], LLM_URL_ALLOW_PRIVATE=False)
    def test_allowlisted_name_passes_regardless_of_resolved_private_ip(self):
        # DNS-rebinding-safe on the allow path: only the operator controls the allowlist.
        with mock.patch("socket.getaddrinfo", return_value=_addrinfo("100.101.102.103")):
            validate_safe_llm_url("http://ollama.example.ts.net/v1")

    @override_settings(LLM_URL_ALLOWLIST=["localhost"], LLM_URL_ALLOW_PRIVATE=False)
    def test_metadata_never_liftable_by_allowlist(self):
        # Even if a host is allowlisted, a resolved link-local (metadata) IP is a hard block.
        with mock.patch("socket.getaddrinfo", return_value=_addrinfo("169.254.169.254")):
            with self.assertRaises(ValidationError):
                validate_safe_llm_url("http://localhost/latest/meta-data/")


class ValidateSafeLLMUrlAllowPrivateTests(TestCase):
    """Blanket self-host escape hatch."""

    @override_settings(LLM_URL_ALLOWLIST=[], LLM_URL_ALLOW_PRIVATE=True)
    def test_blanket_allows_private_and_loopback(self):
        validate_safe_llm_url("http://127.0.0.1:11434/v1")
        validate_safe_llm_url("http://192.168.1.10/v1")

    @override_settings(LLM_URL_ALLOWLIST=[], LLM_URL_ALLOW_PRIVATE=True)
    def test_blanket_does_not_lift_metadata(self):
        with self.assertRaises(ValidationError):
            validate_safe_llm_url("http://169.254.169.254/latest/meta-data/")
