"""The proof-of-work check before anything that can wake the model server (guide 30).

`spa.captcha` issues ALTCHA challenges (`new_challenge`) and lets each solved one through once per
purpose (`check_captcha`); GET /api/spa/public/captcha/ hands them out. The views that need a check
(prewarm, personalise) are tested with the rest of their behaviour in test_portfolio.

Every challenge here is solved in Python by the altcha library's own solver, the same work the
browser's altcha-lib does (frontend/src/lib/captcha.ts), with a tiny difficulty so a test pays
milliseconds.
"""

import base64
import json
import time
from unittest import mock

import altcha
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings

from spa.captcha import check_captcha, new_challenge
from spa.tests._helpers import solved_captcha


def _encode(data) -> str:
    return base64.b64encode(json.dumps(data).encode()).decode()


def _edit(payload: str, change) -> str:
    """The payload decoded, changed in place by `change(dict)`, and encoded again."""
    data = json.loads(base64.b64decode(payload))
    change(data)
    return _encode(data)


class CaptchaChallengeTests(TestCase):
    """What a challenge is: signed, short-lived, and as hard as the settings say."""

    def test_a_challenge_is_what_the_browser_solver_takes(self):
        challenge = new_challenge()
        self.assertEqual(set(challenge), {"parameters", "signature"})
        params = challenge["parameters"]
        # The SPA solves with altcha-lib/algorithms/web/pbkdf2: the browser has PBKDF2 natively.
        self.assertEqual(params["algorithm"], "PBKDF2/SHA-256")
        for key in ("cost", "keyLength", "keyPrefix", "nonce", "salt", "expiresAt"):
            self.assertIn(key, params)
        json.dumps(challenge)  # it goes out as it is

    def test_every_challenge_is_new(self):
        one, two = new_challenge(), new_challenge()
        self.assertNotEqual(one["parameters"]["nonce"], two["parameters"]["nonce"])
        self.assertNotEqual(one["signature"], two["signature"])

    def test_it_expires_after_the_configured_lifetime(self):
        with override_settings(CAPTCHA_LIFETIME_S=600):
            challenge = new_challenge()
        self.assertAlmostEqual(
            challenge["parameters"]["expiresAt"], time.time() + 600, delta=5
        )

    def test_its_answer_lies_below_the_configured_difficulty(self):
        """The answer is a counter below CAPTCHA_MAX_NUMBER (altcha's deterministic mode), so the
        work is bounded: the worst case is the setting, never a run of bad luck."""
        with override_settings(CAPTCHA_MAX_NUMBER=5):
            challenges = [
                altcha.Challenge.from_dict(new_challenge()) for _ in range(10)
            ]
        for challenge in challenges:
            self.assertLess(altcha.solve_challenge(challenge).counter, 5)


class CheckCaptchaTests(TestCase):
    """`check_captcha`: once per solved challenge and purpose, within the challenge's lifetime.
    Everything else is False."""

    def setUp(self):
        cache.clear()  # the single-use records live there

    def test_a_solved_check_passes_once_per_purpose(self):
        """One tick serves the questionnaire's prewarm and its personalise, each once."""
        payload = solved_captcha()
        self.assertTrue(check_captcha(payload, "prewarm"))
        self.assertTrue(check_captcha(payload, "personalise"))
        self.assertFalse(check_captcha(payload, "prewarm"))
        self.assertFalse(check_captcha(payload, "personalise"))

    def test_a_replay_stays_refused(self):
        payload = solved_captcha()
        self.assertTrue(check_captcha(payload, "contact"))
        for _ in range(3):
            self.assertFalse(check_captcha(payload, "contact"))

    def test_single_use_is_kept_in_the_cache_every_worker_shares(self):
        """The record is in Django's cache (Valkey live), not in one process's memory: clearing the
        cache forgets it. An in-process set would let every other worker take the payload again."""
        payload = solved_captcha()
        self.assertTrue(check_captcha(payload, "prewarm"))
        cache.clear()
        self.assertTrue(check_captcha(payload, "prewarm"))

    def test_junk_is_refused(self):
        for junk in (
            "",
            "x",
            "%%% not base64 %%%",
            base64.b64encode(b"hello").decode(),
            _encode([]),
            _encode({"challenge": {}, "solution": {}}),
            "A" * 5000,
        ):
            with self.subTest(junk=junk[:24]):
                self.assertFalse(check_captcha(junk, "prewarm"))

    def test_a_wrong_answer_is_refused(self):
        payload = solved_captcha()

        def wrong_key(d):
            d["solution"]["derivedKey"] = "00" * 32

        def wrong_counter(d):
            d["solution"]["counter"] += 1

        for change in (wrong_key, wrong_counter):
            with self.subTest(change=change.__name__):
                self.assertFalse(check_captcha(_edit(payload, change), "prewarm"))

    def test_a_tampered_challenge_is_refused(self):
        payload = solved_captcha()

        def cheaper(d):
            d["challenge"]["parameters"]["cost"] = 1

        def longer_lived(d):
            d["challenge"]["parameters"]["expiresAt"] += 3600

        def forged(d):
            d["challenge"]["signature"] = "0" * 64

        def unsigned(d):
            del d["challenge"]["signature"]

        for change in (cheaper, longer_lived, forged, unsigned):
            with self.subTest(change=change.__name__):
                self.assertFalse(check_captcha(_edit(payload, change), "prewarm"))

    def test_the_key_comes_from_the_secret_key(self):
        """No setting of its own: a challenge signed under another SECRET_KEY isn't ours."""
        old = "an-earlier-secret-key-" * 3
        payload = solved_captcha(SECRET_KEY=old)
        self.assertFalse(check_captcha(payload, "prewarm"))
        with override_settings(SECRET_KEY=old):
            self.assertTrue(check_captcha(payload, "prewarm"))

    def test_within_its_lifetime_it_passes(self):
        payload = solved_captcha(CAPTCHA_LIFETIME_S=600)
        with mock.patch("time.time", return_value=time.time() + 590):
            self.assertTrue(check_captcha(payload, "prewarm"))

    def test_an_expired_check_is_refused(self):
        payload = solved_captcha(CAPTCHA_LIFETIME_S=600)
        with mock.patch("time.time", return_value=time.time() + 610):
            self.assertFalse(check_captcha(payload, "prewarm"))


class CaptchaEndpointTests(TestCase):
    """GET /api/spa/public/captcha/: anonymous, never cached, throttled per address."""

    URL = "/api/spa/public/captcha/"

    def setUp(self):
        cache.clear()  # scoped-throttle history lives in the default cache

    def test_anyone_gets_a_fresh_challenge(self):
        r = self.client.get(self.URL)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.json()), {"parameters", "signature"})
        # Single use: no cache in between may hand the same one out twice.
        self.assertEqual(r["Cache-Control"], "no-store")
        self.assertEqual(r["X-Robots-Tag"], "noindex")
        again = self.client.get(self.URL)
        self.assertNotEqual(r.json()["signature"], again.json()["signature"])

    def test_what_it_hands_out_passes_the_check(self):
        with override_settings(CAPTCHA_MAX_NUMBER=8):
            r = self.client.get(self.URL)
        challenge = altcha.Challenge.from_dict(r.json())
        payload = altcha.Payload(challenge, altcha.solve_challenge(challenge))
        self.assertTrue(check_captcha(payload.to_base64(), "prewarm"))

    def test_it_only_hands_out(self):
        self.assertEqual(self.client.post(self.URL).status_code, 405)

    def test_it_is_throttled_per_address(self):
        """Each challenge costs the server one key derivation. The bucket is REMOTE_ADDR
        (NUM_PROXIES 0), so a header of the visitor's own doesn't open a new one."""
        rate = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["captcha"]
        allowed = int(rate.split("/")[0])
        codes = [self.client.get(self.URL).status_code for _ in range(allowed + 1)]
        self.assertEqual(codes, [200] * allowed + [429])
        forged = self.client.get(self.URL, HTTP_X_FORWARDED_FOR="198.51.100.7")
        self.assertEqual(forged.status_code, 429)
        elsewhere = self.client.get(self.URL, REMOTE_ADDR="203.0.113.9")
        self.assertEqual(elsewhere.status_code, 200)
