"""Shared fixtures for the spa test package.

Not a `test*.py` module, so the runner never collects it.
"""

from django.test import override_settings


def solved_captcha(**settings_overrides) -> str:
    """A challenge from `spa.captcha.new_challenge`, solved the way the browser solves it (the
    altcha solver, PBKDF2) and encoded as a request's `captcha` field carries it. The difficulty
    is tiny, so a test pays a few milliseconds and not a visitor's second. `settings_overrides`
    apply while the challenge is made (a lifetime, a SECRET_KEY).

    The imports sit inside, so the modules that use this still import while `spa.captcha` (guide
    30) doesn't exist: only the tests that need a solved check go red."""
    import altcha

    from spa.captcha import new_challenge

    with override_settings(CAPTCHA_MAX_NUMBER=8, **settings_overrides):
        challenge = altcha.Challenge.from_dict(new_challenge())
    solution = altcha.solve_challenge(challenge)
    return altcha.Payload(challenge, solution).to_base64()
