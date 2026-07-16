"""Shared fixtures for the jac test package.

Not a `test*.py` module, so the runner never collects it. Importing it also pulls
in `llm_connector.tests._helpers`, which registers the in-memory `fake` /
`fakesearch` providers the pipeline tests resolve executors against.
"""

import logging
from contextlib import contextmanager

from django.contrib.auth.models import User

# Side effect: registers the fake providers + exports the shared seed/factory.
from llm_connector.tests._helpers import TEST_HIRSCHAI, fake_row  # noqa: F401

from jac.models import JobApplication, JobPosting

POSTING = (
    "Acme GmbH is hiring a Python Backend Engineer in Berlin. You will build "
    "Django REST services and PostgreSQL data models, and deploy them on Linux."
)


@contextmanager
def _muted():
    """Silence logging inside the block — ONLY around deliberately-noisy paths,
    so an unexpected line in the test output always means something is off."""
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


def make_user(name="alice"):
    return User.objects.create_user(username=name, password="pw")


def make_application(user, text=POSTING):
    posting = JobPosting.objects.create(user=user, posting_text=text)
    return JobApplication.objects.create(user=user, posting=posting)
