"""Throwaway media roots for the test classes that write real files."""

import atexit
import shutil
import tempfile


def own_media_roots() -> dict:
    """`@override_settings(**own_media_roots())`: a pair of dirs for one test class.

    Per class, not per module. Forked parallel workers (Linux CI) inherit whatever a module made at
    import, and a worker tears the module down after each class it runs, so a shared dir gets pulled
    from under another class. Removed at exit instead: a forked worker leaves through os._exit and
    skips atexit, so only the process that made a dir removes it.
    """
    roots = {
        "MEDIA_ROOT": tempfile.mkdtemp(prefix="jac-media-tests-"),
        "PRIVATE_MEDIA_ROOT": tempfile.mkdtemp(prefix="jac-private-tests-"),
    }
    for path in roots.values():
        atexit.register(shutil.rmtree, path, ignore_errors=True)
    return roots
