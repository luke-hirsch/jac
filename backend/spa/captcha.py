"""A proof-of-work check before anything that can wake the model server: ALTCHA, self-hosted.

The browser fetches a challenge (`new_challenge`, GET /api/spa/public/captcha/), solves it with
altcha-lib (frontend/src/lib/captcha.ts) and sends the payload in the request's `captcha` field.
`check_captcha` lets each payload through once per purpose.

What it stops: scripted POSTs that run no solver, and it makes every wake cost the client CPU. What
it doesn't stop: a headless browser, or a script with an ALTCHA library, solves it as a visitor's
browser does. The throttles stay the real limit (`prewarm`, `portfolio-personalise`, and the
site-wide prewarm cooldown in llm_connector/tasks.py).

No outside service, no cookie, no new secret: the signing key derives from SECRET_KEY, and the
single-use record is a cache key named by the challenge's signature, gone when the challenge
expires. It says nothing about the visitor.
"""

import secrets
import time

import altcha
from django.conf import settings
from django.core.cache import cache
from django.utils.crypto import salted_hmac

# PBKDF2, because every browser has it natively (crypto.subtle): the SPA solves with
# altcha-lib/algorithms/web/pbkdf2.
ALGORITHM = "PBKDF2/SHA-256"
# Iterations per try: about half a millisecond in a browser on a recent laptop. The number of
# tries is below CAPTCHA_MAX_NUMBER, so the two together are the visitor's wait.
COST = 5_000
# A payload is about 600 characters. Anything far longer isn't one.
MAX_PAYLOAD_LEN = 2_000
# What a refused check answers, as a field error on `captcha`.
REFUSED = "The check is missing, expired or used already."


def _key() -> str:
    """The key that signs every challenge, derived from SECRET_KEY: no setting of its own, and a
    new SECRET_KEY only voids the challenges out there, which live minutes."""
    return salted_hmac("spa.captcha", "challenge", algorithm="sha256").hexdigest()  # type: ignore


def new_challenge() -> dict:
    """A signed challenge for the browser's solver (altcha-lib's `solveChallenge`). Deterministic
    mode: the answer is a counter below CAPTCHA_MAX_NUMBER, so the work is bounded, never a run of
    bad luck."""
    challenge = altcha.create_challenge(
        ALGORITHM,
        COST,
        counter=secrets.randbelow(settings.CAPTCHA_MAX_NUMBER),
        expires_at=int(time.time()) + settings.CAPTCHA_LIFETIME_S,
        hmac_secret=_key(),
    )
    return challenge.to_dict()


def check_captcha(payload: str, purpose: str) -> bool:
    """A solved challenge of ours, unexpired, used for `purpose` the first time? One payload serves
    one prewarm and one personalise (and one contact message), each once. Junk, a foreign or
    tampered signature, a wrong answer, an expired challenge and a replay are all False."""
    if not isinstance(payload, str) or len(payload) > MAX_PAYLOAD_LEN:
        return False
    if not altcha.verify_solution(payload, _key()).verified:
        return False
    # Verified, so this parses, and the expiry is the one we signed.
    challenge = altcha.Payload.from_base64(payload).challenge
    left = int(challenge.parameters.expires_at) - int(time.time())  # type: ignore
    # Only now: junk never reaches the cache. add() is atomic (SET NX in Valkey), so two workers
    # racing for one payload can't both win.
    return cache.add(f"spa.captcha:{purpose}:{challenge.signature}", 1, max(1, left))
