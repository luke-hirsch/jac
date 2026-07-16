import time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from llm_connector.catalog import default_model
from llm_connector.conf import HIRSCHAI_PROVIDER
from llm_connector.executor import Executor
from llm_connector.models import LLMConfig
from llm_connector.probe import hirschai_reachable


class Command(BaseCommand):
    help = (
        "Round-trip check: HirschAI (probe + pong) and a user's configured providers."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--user", type=int, help="Also check this user's provider rows."
        )

    def handle(self, *args, **opts):
        reachable = hirschai_reachable(refresh=True)
        self.stdout.write(f"HirschAI: {'reachable' if reachable else 'OFFLINE'}")
        if reachable:
            self._pong(Executor(HIRSCHAI_PROVIDER))
        if opts.get("user"):
            user = self._get_user(opts["user"])  # raise CommandError on unknown pk
            for row in LLMConfig.objects.filter(user=user):
                self._pong(Executor(row.provider, default_model(row.provider), user))

    def _pong(self, executor):
        start = time.monotonic()
        try:
            executor.complete("Respond with exactly one word: pong")
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(f"  {executor.provider}: FAILED — {exc}")
            return
        ms = int((time.monotonic() - start) * 1000)
        self.stdout.write(
            f"  {executor.provider} model={executor.model or '(row default)'} {ms}ms"
        )

    def _get_user(self, pk):
        User = get_user_model()
        return User.objects.get(pk=pk)
