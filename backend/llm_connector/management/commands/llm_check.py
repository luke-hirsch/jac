import time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from llm_connector.catalog import COMMERCIAL_PROVIDERS, default_model
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
        reachable = hirschai_reachable()
        self.stdout.write(f"HirschAI: {'reachable' if reachable else 'OFFLINE'}")
        if reachable:
            self._pong(Executor(HIRSCHAI_PROVIDER))
        if opts.get("user"):
            user = self._get_user(opts["user"])  # raise CommandError on unknown pk
            for row in LLMConfig.objects.filter(user=user):
                # The default pick from the key's stored model list (Check refreshes it). Not
                # resolve_executor: that's the API's gate, and it only knows the real providers.
                model = default_model(row.provider, row.models)
                if model is None and row.provider in COMMERCIAL_PROVIDERS:
                    self.stdout.write(
                        f"  {row.provider}: FAILED — no model list yet: run Check on the key."
                    )
                    continue
                self._pong(Executor(row.provider, model, user))

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
