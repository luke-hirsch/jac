"""Test runner adding `--skip <group> [<group> …]` to `manage.py test`.

Everything runs by default; `--skip` opts *out* of named groups. That direction is
deliberate: a forgotten `--skip` costs time, a forgotten `--include` costs coverage.

    python manage.py test                      # all 446
    python manage.py test --skip llm           # 261s → ~21s
    python manage.py test --skip llm auth      # ~15s
    python manage.py test --skip llm --skip auth   # same thing

A group is one Django test tag (see `django.test.tag`), so `--skip llm` is exactly
`--exclude-tag llm` with a name you can remember and a typo check on top.

## What earns a group

Cost or an external dependency — never feature area. "I only care about the CV
pipeline" is already served by `manage.py test jac.tests.test_pipeline`; mirroring
the app tree in here would just grow a second, staler copy of it. So:

- it needs something outside the test process to be up (a model, a service), or
- it is a measurably expensive block of the suite (>~2s as of 2026-08-09).

Measure before adding one: `manage.py test --durations 20` names the slowest tests.
"""

from django.test.runner import DiscoverRunner

# group name → why it exists. The name is also the test tag.
SKIP_GROUPS = {
    "llm": (
        "live-model prompt tests — need ollama/the tower answering. 8 tests, ~237s: "
        "91% of the whole suite (jac/tests/test_prompts.py)"
    ),
    "auth": (
        "allauth signup/login, MFA and the admin gate — the slowest non-live block "
        "(~6s), most of it PBKDF2 password hashing"
    ),
    "files": (
        "CV/application attachment round-trips through MEDIA_ROOT — real files on "
        "disk, ~3s"
    ),
}


class SkipGroupRunner(DiscoverRunner):
    """`DiscoverRunner` + `--skip`, which folds group names into `exclude_tags`."""

    @classmethod
    def add_arguments(cls, parser):
        super().add_arguments(parser)
        parser.add_argument(
            "--skip",
            dest="skip_groups",
            nargs="+",
            action="append",
            choices=sorted(SKIP_GROUPS),
            metavar="GROUP",
            help=(
                "Skip whole groups of tests. Repeatable and space-separated. "
                + " · ".join(f"{name}: {why}" for name, why in SKIP_GROUPS.items())
            ),
        )

    def __init__(self, *args, skip_groups=None, **kwargs):
        super().__init__(*args, **kwargs)
        # action="append" + nargs="+" gives a list of lists — flatten it. argparse's
        # `choices` already rejected anything not in SKIP_GROUPS, so no validation here.
        groups = {g for batch in (skip_groups or ()) for g in batch}
        # super() set this from --exclude-tag; union rather than replace so both work.
        self.exclude_tags |= groups
