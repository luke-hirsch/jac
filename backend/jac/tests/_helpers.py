"""Shared fixtures for the jac test package.

Not a `test*.py` module, so the test runner never collects it. Test modules
pull what they need from here (`from ._helpers import ...`); nothing in the
suite redefines these locally anymore.
"""

import logging
from contextlib import contextmanager

from jac.cv import CV
from jac.models import JobApplication, JobPosting


@contextmanager
def _muted():
    """Silence logging inside the block. Wrap ONLY the LLM error-path tests,
    which deliberately trigger `logger.exception(...)`. Logging anywhere else
    still surfaces — so an unexpected traceback in the run output always means
    something is genuinely off, never an expected error path."""
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


def _entry(id, type, *, text="", refs=None, favourite=False):
    """A flattened CV entry, shaped like CV._flatten_entries output. The selection
    code reads `favourite`/`refs` via .get(), so the defaults are always safe."""
    return {
        "id": id,
        "type": type,
        "text": text,
        "refs": refs or [],
        "favourite": favourite,
    }


def _cv_with(user_pk, *, jobs=None):
    """A CV whose entries are injected directly (bypassing the DB query layer),
    used by the cover-letter tests. Only the kept `jobs` ever vary."""
    cv = CV(user_pk=user_pk)
    cv.entries = {
        "jobs": jobs or [],
        "projects": [],
        "skills": [],
        "educations": [],
        "certifications": [],
        "languages": [],
    }
    return cv


def _job_posting(user, *, language="en", title="Backend Engineer", posting_text="We need a dev."):
    """An unsaved JobPosting for cover-letter tests."""
    return JobPosting(
        user=user, title=title, posting_text=posting_text, language=language
    )


def _application(user, *, posting_text="We need a dev.", title="", **kw):
    """A persisted JobPosting + JobApplication pair — the fixture every
    generation/application test hangs its `GenerationRun`s off."""
    posting = JobPosting.objects.create(
        user=user, posting_text=posting_text, title=title
    )
    return JobApplication.objects.create(user=user, posting=posting, **kw)


class _CoverLetterCVMixin:
    """Shared cover-letter fixture: a CV with only `self.job` kept, plus a
    JobPosting factory bound to `self.user`. Every cover-letter test class used
    to redefine these identically."""

    def _cv(self):
        return _cv_with(self.user.pk, jobs=[self.job])

    def _jp(self, **kw):
        return _job_posting(self.user, **kw)


class _StubSnippet:
    """Minimal stand-in for a ResumeSnippet in writer/verifier prompt tests."""

    def __init__(self, title, content, kind_display="Achievement"):
        self.title = title
        self.content = content
        self._kind_display = kind_display

    def get_kind_display(self):
        return self._kind_display


def _keep_all(self, job_post_text, grade=None):
    """Stand-in for CV.filter_cv: keep every flattened entry, score 1.0."""
    out: dict = {}
    for e in self._flatten_entries():
        out.setdefault(e["type"], []).append({**e, "score": 1.0})
    return out
