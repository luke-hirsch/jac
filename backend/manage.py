#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    # `manage.py test` runs on lukehirsch.test_settings (production settings + a fast
    # password hasher). Set here rather than sniffed inside settings.py because spawned
    # --parallel workers inherit os.environ but not sys.argv; going through
    # DJANGO_SETTINGS_MODULE is what carries the choice into them.
    is_test = sys.argv[1:2] == ['test']
    os.environ.setdefault(
        'DJANGO_SETTINGS_MODULE',
        'lukehirsch.test_settings' if is_test else 'lukehirsch.settings',
    )
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
