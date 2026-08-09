"""Settings for test runs: production settings with a throwaway password hasher.

`manage.py test` selects this module automatically (see manage.py) — there is no flag to
remember. Everything else is inherited, so this file must never grow into a second
configuration: if a test needs different settings, it uses `override_settings`.

Why a separate module rather than a branch inside settings.py or a patch in the test
runner: `--parallel` workers are *spawned* on macOS, so each one boots a fresh
interpreter that re-imports settings from scratch. It inherits `os.environ` (hence
DJANGO_SETTINGS_MODULE) but not `sys.argv`, and never touches the parent's runner
instance — so a `"test" in sys.argv` guard or a `setup_test_environment()` patch would
silently leave parallel workers on the slow hasher. `SkipGroupRunnerTests` asserts the
fast hasher is actually in force, and the suite is run both ways to prove it.
"""

from .settings import *

# Django's own recommendation for test speed (topics/testing/overview → "Password
# hashing"): the production Argon2 hasher is deliberately memory-hard, and the suite has
# ~72 create_user/login sites. Measured 2026-08-09: PBKDF2 130.5ms vs MD5 0.03ms per hash.
# Safe because the test DB is created and destroyed inside the run — nothing here ever
# stores a real password. NEVER import this module outside a test run.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]
