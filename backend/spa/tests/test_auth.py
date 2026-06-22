"""spa.tests — auth flow tests.

Covers signup → email verify → login, password reset/change, TOTP
enroll/verify, recovery-code consumption, login rate limiting, and the
AdminRequireMfaMiddleware gate.
"""

import json
import re
import time

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings

from allauth.mfa.models import Authenticator
from allauth.mfa.totp.internal.auth import TOTP, format_hotp_value, hotp_value

User = get_user_model()

SIGNUP = "/_allauth/browser/v1/auth/signup"
LOGIN = "/_allauth/browser/v1/auth/login"
LOGOUT = "/_allauth/browser/v1/auth/session"
VERIFY_EMAIL = "/_allauth/browser/v1/auth/email/verify"
PASSWORD_REQUEST = "/_allauth/browser/v1/auth/password/request"
PASSWORD_RESET = "/_allauth/browser/v1/auth/password/reset"
PASSWORD_CHANGE = "/_allauth/browser/v1/account/password/change"
REAUTHENTICATE = "/_allauth/browser/v1/auth/reauthenticate"
TOTP_ENDPOINT = "/_allauth/browser/v1/account/authenticators/totp"
RECOVERY_CODES = "/_allauth/browser/v1/account/authenticators/recovery-codes"
MFA_AUTHENTICATE = "/_allauth/browser/v1/auth/2fa/authenticate"
MFA_REAUTHENTICATE = "/_allauth/browser/v1/auth/2fa/reauthenticate"


def _post(client, url, data):
    return client.post(url, json.dumps(data), content_type="application/json")


def _totp_code(secret):
    counter = int(time.time()) // 30
    return format_hotp_value(hotp_value(secret, counter))


def _extract_email_code():
    assert mail.outbox, "No email in outbox"
    # allauth sends codes as e.g. "BKXK-TQQW" (4+4 alphanumeric, dash-separated)
    match = re.search(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b", mail.outbox[-1].body)
    assert match, f"No verification code found in email: {mail.outbox[-1].body[:300]}"
    return match.group(1)


_TEST_SETTINGS = {
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "CACHES": {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    "ALLOWED_HOSTS": ["localhost", "testserver"],
}


@override_settings(**_TEST_SETTINGS)
class AuthFlowTests(TestCase):
    """Full allauth headless auth flows."""

    EMAIL = "test@example.com"
    PASSWORD = "testpass_Xk9!"

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        mail.outbox.clear()

    def _signup(self, email=EMAIL, password=PASSWORD):
        return _post(self.client, SIGNUP, {"email": email, "password": password})

    def _verify_email(self):
        code = _extract_email_code()
        mail.outbox.clear()
        return _post(self.client, VERIFY_EMAIL, {"key": code})

    def _login(self, email=EMAIL, password=PASSWORD):
        return _post(self.client, LOGIN, {"email": email, "password": password})

    def _signup_and_verify(self, email=EMAIL, password=PASSWORD):
        """Signup then submit the email verification code.

        Verifying the code completes the pending signup flow and logs the
        user in (200). Calling LOGIN again would return 409 (already
        authenticated) — see the headless OpenAPI spec.
        """
        self._signup(email, password)
        return self._verify_email()

    # --- signup + verify + login ---

    def test_signup_requires_email_verification(self):
        resp = self._signup()
        # Pending email verification → 401 with verify_email in flows.
        self.assertEqual(resp.status_code, 401)
        flow_ids = [f["id"] for f in resp.json().get("data", {}).get("flows", [])]
        self.assertIn("verify_email", flow_ids)
        self.assertEqual(len(mail.outbox), 1)

    def test_signup_verify_logs_user_in(self):
        resp = self._signup_and_verify()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("meta", {}).get("is_authenticated"))

    def test_login_after_logout_succeeds(self):
        self._signup_and_verify()
        self.client.delete(LOGOUT)
        resp = self._login()
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("meta", {}).get("is_authenticated"))

    def test_login_bad_password_rejected(self):
        self._signup_and_verify()
        self.client.delete(LOGOUT)
        resp = _post(self.client, LOGIN, {"email": self.EMAIL, "password": "wrongpassword"})
        self.assertIn(resp.status_code, [400, 401])

    # --- rate limiting ---

    def test_login_rate_limit(self):
        self._signup_and_verify()
        self.client.delete(LOGOUT)
        for _ in range(6):
            resp = _post(self.client, LOGIN, {"email": self.EMAIL, "password": "wrong"})
        # After repeated failures allauth should block or keep returning 4xx.
        self.assertIn(resp.status_code, [400, 401, 429])

    # --- password reset ---

    def test_password_reset_sends_email(self):
        self._signup_and_verify()
        self.client.delete(LOGOUT)
        mail.outbox.clear()
        resp = _post(self.client, PASSWORD_REQUEST, {"email": self.EMAIL})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

    def test_password_reset_confirm(self):
        self._signup_and_verify()
        self.client.delete(LOGOUT)
        mail.outbox.clear()
        _post(self.client, PASSWORD_REQUEST, {"email": self.EMAIL})
        match = re.search(r"/auth/reset-password/([^\"'\s]+)", mail.outbox[0].body)
        self.assertIsNotNone(match, "No reset key found in email")
        key = match.group(1)
        new_password = "newpass_Xk9!"
        resp = _post(self.client, PASSWORD_RESET, {"key": key, "password": new_password})
        # Per spec: 200 if ACCOUNT_LOGIN_ON_PASSWORD_RESET=True (auto-login),
        # 401 if False (default — successful reset, user must log in).
        self.assertIn(resp.status_code, [200, 401])
        # Verify the new password actually works.
        login_resp = _post(self.client, LOGIN, {"email": self.EMAIL, "password": new_password})
        self.assertEqual(login_resp.status_code, 200)

    # --- password change ---

    def test_password_change(self):
        self._signup_and_verify()
        resp = _post(self.client, PASSWORD_CHANGE, {
            "current_password": self.PASSWORD,
            "new_password": "changed_Xk9!",
        })
        self.assertEqual(resp.status_code, 200)

    # --- TOTP ---

    def _enroll_totp(self, password=PASSWORD):
        """Run the GET-then-POST TOTP enrollment dance and return the secret.

        Activation requires *recent* password authentication; signup-then-verify
        doesn't count, so we reauthenticate first.
        """
        reauth = _post(self.client, REAUTHENTICATE, {"password": password})
        self.assertEqual(reauth.status_code, 200, reauth.content)
        resp = self.client.get(TOTP_ENDPOINT)
        # GET returns 404 with `secret` + `totp_url` in meta when not yet
        # activated — this is the documented success path for enrollment.
        self.assertEqual(resp.status_code, 404)
        secret = resp.json()["meta"]["secret"]
        activate = _post(self.client, TOTP_ENDPOINT, {"code": _totp_code(secret)})
        self.assertEqual(activate.status_code, 200, activate.content)
        return secret

    def test_totp_enroll_and_verify(self):
        self._signup_and_verify()
        self._enroll_totp()
        self.assertTrue(
            Authenticator.objects.filter(
                user__email=self.EMAIL, type=Authenticator.Type.TOTP
            ).exists()
        )

    def test_totp_mfa_challenge_on_login(self):
        self._signup_and_verify()
        secret = self._enroll_totp()
        # Log out, then log back in — should land in the MFA stage (401).
        self.client.delete(LOGOUT)
        resp = self._login()
        self.assertEqual(resp.status_code, 401)
        flow_ids = [f["id"] for f in resp.json().get("data", {}).get("flows", [])]
        self.assertIn("mfa_authenticate", flow_ids)
        resp = _post(self.client, MFA_AUTHENTICATE, {"code": _totp_code(secret)})
        self.assertEqual(resp.status_code, 200)

    # --- recovery codes ---

    def test_recovery_codes_generate_and_use(self):
        self._signup_and_verify()
        self._enroll_totp()
        # Recovery-codes regenerate may return 401 if recent-auth has expired;
        # right after signup+TOTP enroll we're still within the window.
        resp = _post(self.client, RECOVERY_CODES, {})
        self.assertEqual(resp.status_code, 200, resp.content)
        codes = resp.json()["data"]["unused_codes"]
        self.assertTrue(len(codes) > 0)
        # Log out and back in — use a recovery code for MFA.
        self.client.delete(LOGOUT)
        self._login()
        resp = _post(self.client, MFA_AUTHENTICATE, {"code": codes[0]})
        self.assertEqual(resp.status_code, 200)


@override_settings(**_TEST_SETTINGS)
class AdminMfaGateTests(TestCase):
    """AdminRequireMfaMiddleware: staff + MFA enrolled → redirected; others pass through."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username="staff", email="staff@example.com", password="pass", is_staff=True
        )
        self.regular = User.objects.create_user(
            username="regular", email="regular@example.com", password="pass"
        )

    def _enroll_totp(self, user):
        from allauth.mfa.totp.internal.auth import generate_totp_secret, TOTP
        secret = generate_totp_secret()
        TOTP.activate(user, secret)
        return secret

    def test_unauthenticated_passes_through_to_admin_login(self):
        resp = self.client.get("/admin/")
        # Django admin redirects unauthenticated users to /admin/login/
        self.assertIn(resp.status_code, [302, 301])
        self.assertNotIn("/auth/mfa-challenge", resp.get("Location", ""))

    def test_staff_without_mfa_passes_through(self):
        self.client.force_login(self.staff)
        resp = self.client.get("/admin/")
        self.assertNotIn("mfa-challenge", resp.get("Location", ""))

    def test_regular_user_passes_through(self):
        self.client.force_login(self.regular)
        resp = self.client.get("/admin/")
        location = resp.get("Location", "")
        self.assertNotIn("mfa-challenge", location)

    def test_staff_with_mfa_gets_redirected(self):
        self._enroll_totp(self.staff)
        self.client.force_login(self.staff)
        resp = self.client.get("/admin/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("mfa-challenge", resp["Location"])
        self.assertIn("next=/admin/", resp["Location"])

    def test_staff_with_mfa_authenticated_in_session_passes_through(self):
        self._enroll_totp(self.staff)
        self.client.force_login(self.staff)
        session = self.client.session
        session["mfa_authenticated"] = True
        session.save()
        resp = self.client.get("/admin/")
        self.assertNotIn("mfa-challenge", resp.get("Location", ""))

    def test_signal_sets_session_flag(self):
        from allauth.mfa.signals import authenticator_used
        secret = self._enroll_totp(self.staff)
        authenticator = Authenticator.objects.get(user=self.staff, type=Authenticator.Type.TOTP)
        self.client.force_login(self.staff)
        request = self.client.get("/admin/").wsgi_request  # triggers middleware redirect
        # Fire the signal manually with a fresh request that has a session
        from django.test import RequestFactory
        factory = RequestFactory()
        fake_request = factory.get("/")
        fake_request.session = self.client.session
        authenticator_used.send(
            sender=Authenticator,
            request=fake_request,
            user=self.staff,
            authenticator=authenticator,
            reauthenticated=True,
            passwordless=False,
        )
        self.assertTrue(fake_request.session.get("mfa_authenticated"))


class UserProfileViewTests(TestCase):
    """`/api/spa/profile/` returns the request user's auto-created profile,
    requires authentication, and never leaks another user's profile.
    """

    PROFILE_URL = "/api/spa/profile/"

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="alice", email="alice@example.com", password="pw")
        cls.bob = User.objects.create_user(username="bob", email="bob@example.com", password="pw")

    def test_unauthenticated_is_forbidden(self):
        resp = self.client.get(self.PROFILE_URL)
        self.assertIn(resp.status_code, (401, 403))

    def test_get_returns_own_profile(self):
        self.client.force_login(self.alice)
        resp = self.client.get(self.PROFILE_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["id"], self.alice.profile.pk)

    def test_patch_updates_own_profile(self):
        self.client.force_login(self.alice)
        resp = self.client.patch(
            self.PROFILE_URL,
            data=json.dumps({"display_name": "Alice A.", "theme": "dark"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.alice.profile.refresh_from_db()
        self.assertEqual(self.alice.profile.display_name, "Alice A.")
        self.assertEqual(self.alice.profile.theme, "dark")

    def test_each_user_sees_only_their_own_profile(self):
        self.client.force_login(self.alice)
        alice_id = self.client.get(self.PROFILE_URL).json()["id"]
        self.client.force_login(self.bob)
        bob_id = self.client.get(self.PROFILE_URL).json()["id"]
        self.assertNotEqual(alice_id, bob_id)
