"""SSRF URL validator for user-supplied LLM endpoints.

Red until `[backend]-ssrf-signup-gate` lands `llm_connector/validators.py`. Loopback/private
literals need no DNS; the hostname->internal case patches socket.getaddrinfo so the suite stays
offline and deterministic.
"""

from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase

from llm_connector.validators import validate_safe_llm_url


def _addrinfo(ip):
    # Shape of socket.getaddrinfo()[i] — only [4][0] (the IP) is read by the validator.
    return [(2, 1, 6, "", (ip, 0))]


class ValidateSafeLLMUrlTests(TestCase):
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
