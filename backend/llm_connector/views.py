"""DRF viewsets for the LLM connector.

`LLMConfigViewSet` exposes the per-user alias configs (with a write-only
`api_key`). `LLMRequestLogViewSet` is the read-only spend audit. Both are
scoped to `request.user` via `get_queryset`, with `IsOwner` layered on as
defense-in-depth against custom actions that bypass the queryset.
`LLMAliasListView` is the resolved capability view the generation UI pairs
grades and models with.
"""

import time

from drf_spectacular.utils import OpenApiResponse, extend_schema
from lukehirsch.permissions import IsOwner
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from llm_connector.base import LLMAdapter
from llm_connector.client import LLMClient
from llm_connector.conf import FALLBACK_ALIAS, get_alias_config, get_alias_strength
from llm_connector.models import LLMConfig, LLMGradePin, LLMRequestLog
from llm_connector.registry import get_adapter_class
from llm_connector.serializers import LLMConfigSerializer, LLMRequestLogSerializer


class LLMConfigViewSet(viewsets.ModelViewSet):
    serializer_class = LLMConfigSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return LLMConfig.objects.filter(user=self.request.user)

    @extend_schema(
        request=None,
        responses=OpenApiResponse(
            description="{ok: true, latency_ms} or {ok: false, error}"
        ),
    )
    @action(detail=True, methods=["post"])
    def check(self, request, pk=None):
        """Connectivity probe for one config — the API twin of `llm_check`.

        Round-trips a one-word completion through the row's alias exactly as the
        pipeline would resolve it. A failed probe is a *result*, not an HTTP
        error: both outcomes are 200 with an `ok` discriminator.
        """
        config = self.get_object()
        try:
            client = LLMClient(config.alias, user=request.user)
            start = time.monotonic()
            client.complete("Respond with exactly one word: pong")
            latency_ms = int((time.monotonic() - start) * 1000)
            return Response({"ok": True, "latency_ms": latency_ms})
        except Exception as exc:  # noqa: BLE001 — any failure is the check's finding
            return Response({"ok": False, "error": str(exc)})


class LLMRequestLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = LLMRequestLogSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return LLMRequestLog.objects.filter(user=self.request.user)


def _adapter_capabilities(provider: str) -> tuple[bool, bool]:
    """(supports_embed, supports_web_search) for a provider, from the adapter class
    without instantiating it. `embed` is an optional override on the base class, so
    an identity check on the unbound method is the capability flag. Unknown provider
    or an unimportable SDK → (False, False): the UI must not offer what won't run."""
    try:
        cls = get_adapter_class(provider)
    except Exception:  # noqa: BLE001 — missing optional SDK / unknown provider
        return False, False
    return (
        cls.embed is not LLMAdapter.embed,
        bool(getattr(cls, "supports_web_search", False)),
    )


class LLMPinView(APIView):
    """The user's per-strength favourite models ("pins"), as one dict.

    GET  -> {"light": alias | null, "standard": …, "strong": …}
    PUT  {"strength": "<tier>", "alias": "<alias>" | ""} — upserts the tier's pin;
         a blank alias clears it. The alias must be one of the user's configured
         aliases or the "default" fallback. Response: the full pins dict.
    """

    permission_classes = [IsAuthenticated]

    def _pins(self, user) -> dict:
        stored = dict(
            LLMGradePin.objects.filter(user=user).values_list("strength", "alias")
        )
        return {s: stored.get(s) for s in LLMGradePin.Strength.values}

    @extend_schema(
        responses=OpenApiResponse(description='{"light": alias|null, "standard": …, "strong": …}')
    )
    def get(self, request):
        return Response(self._pins(request.user))

    @extend_schema(
        request=None,
        responses=OpenApiResponse(description="the full pins dict after the change"),
    )
    def put(self, request):
        strength = request.data.get("strength")
        alias = (request.data.get("alias") or "").strip()
        if strength not in LLMGradePin.Strength.values:
            return Response(
                {"strength": [f"Must be one of {LLMGradePin.Strength.values}."]},
                status=400,
            )
        if alias:
            known = set(
                LLMConfig.objects.filter(user=request.user).values_list(
                    "alias", flat=True
                )
            )
            known.add(FALLBACK_ALIAS)
            if alias not in known:
                return Response(
                    {"alias": ["Not one of your configured aliases."]}, status=400
                )
            LLMGradePin.objects.update_or_create(
                user=request.user, strength=strength, defaults={"alias": alias}
            )
        else:
            LLMGradePin.objects.filter(user=request.user, strength=strength).delete()
        return Response(self._pins(request.user))


class LLMAliasListView(APIView):
    """The user's aliases with their *resolved* pipeline capabilities: strength
    (light/standard/strong), embedding support (the light rung's hard requirement)
    and web search (the personal paragraph's). Always includes the "default"
    fallback alias, which resolves to settings.LLM when the user has no row of
    that name — exactly what the pipeline itself will do with it."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses=OpenApiResponse(
            description="[{alias, provider, model, strength, "
            "supports_embed, supports_web_search}]"
        )
    )
    def get(self, request):
        aliases = set(
            LLMConfig.objects.filter(user=request.user).values_list("alias", flat=True)
        )
        aliases.add(FALLBACK_ALIAS)
        rows = []
        for alias in sorted(aliases):
            try:
                config = get_alias_config(alias, user=request.user)
            except Exception:  # noqa: BLE001 — a broken row must not hide the rest
                continue
            provider = config.get("provider", "")
            supports_embed, supports_web_search = _adapter_capabilities(provider)
            rows.append(
                {
                    "alias": alias,
                    "provider": provider,
                    "model": config.get("model", ""),
                    "strength": get_alias_strength(alias, user=request.user),
                    "supports_embed": supports_embed,
                    "supports_web_search": supports_web_search,
                }
            )
        return Response(rows)
