import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from llm_connector.client import LLMClient
from llm_connector.conf import get_llm_settings
from llm_connector.models import LLMConfig


class Command(BaseCommand):
    help = (
        "Verify connectivity for LLM aliases (parallel). "
        "Without --user, checks settings.LLM (the global fallback). "
        "With --user, checks every LLMConfig that user owns."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "aliases",
            nargs="*",
            help=(
                "Specific aliases to check (default: all available for the "
                "chosen scope). With --user, restricts to those of the user's "
                "configured aliases."
            ),
        )
        parser.add_argument(
            "--user",
            type=int,
            help="User pk: check that user's LLMConfig rows instead of settings.LLM.",
        )

    def _check_alias(self, alias: str, provider: str, model: str, user) -> dict:
        try:
            client = LLMClient(alias, user=user)
            start = time.monotonic()
            client.complete("Respond with exactly one word: pong")
            latency = int((time.monotonic() - start) * 1000)
            return {"alias": alias, "provider": provider, "model": model, "latency": latency}
        except Exception as exc:
            return {"alias": alias, "provider": provider, "model": model, "error": str(exc)}

    def _resolve_targets(self, requested_aliases, user) -> tuple[list[tuple[str, str, str]], list[str]]:
        """Returns (targets, missing) where targets is [(alias, provider, model), ...]."""
        if user is not None:
            available = {
                cfg.alias: (cfg.provider, cfg.model)
                for cfg in LLMConfig.objects.filter(user=user)
            }
            if not available:
                return [], requested_aliases or []
            aliases = requested_aliases or sorted(available)
            targets = [(a, *available[a]) for a in aliases if a in available]
            missing = [a for a in aliases if a not in available]
            return targets, missing

        llm = get_llm_settings()
        aliases = requested_aliases or list(llm)
        targets = [
            (a, llm[a].get("provider", "?"), llm[a].get("model", "?"))
            for a in aliases if a in llm
        ]
        missing = [a for a in aliases if a not in llm]
        return targets, missing

    def handle(self, *args, **options):
        user = None
        if options.get("user") is not None:
            User = get_user_model()
            try:
                user = User.objects.get(pk=options["user"])
            except User.DoesNotExist as exc:
                raise CommandError(f"No user with pk={options['user']}") from exc

        try:
            targets, missing = self._resolve_targets(options["aliases"], user)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return

        scope = f"user={user}" if user else "settings.LLM"
        self.stdout.write(f"Checking {len(targets)} alias(es) in parallel [{scope}]...\n")

        for alias in missing:
            self.stdout.write(
                f"  [{alias}] " + self.style.WARNING(
                    "not configured for this user" if user else "not found in settings.LLM"
                )
            )

        results: dict[str, dict] = {}
        if targets:
            with ThreadPoolExecutor(max_workers=len(targets)) as pool:
                futures = {
                    pool.submit(self._check_alias, a, p, m, user): a
                    for a, p, m in targets
                }
                for future in as_completed(futures):
                    result = future.result()
                    results[result["alias"]] = result

        for alias, _, _ in targets:
            if alias not in results:
                continue
            r = results[alias]
            self.stdout.write(f"  [{alias}] ", ending="")
            if "error" in r:
                self.stdout.write(
                    self.style.ERROR("FAIL")
                    + f"  provider={r['provider']}  model={r['model']}  error={r['error']}"
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS("OK")
                    + f"  provider={r['provider']}  model={r['model']}  latency={r['latency']}ms"
                )
