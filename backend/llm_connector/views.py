"""DRF viewsets for the LLM connector.

`LLMConfigViewSet` exposes the per-user alias configs (with a write-only
`api_key`). `LLMRequestLogViewSet` is the read-only spend audit. Both are
scoped to `request.user` via `get_queryset`, with `IsOwner` layered on as
defense-in-depth against custom actions that bypass the queryset.
"""

from lukehirsch.permissions import IsOwner
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from llm_connector.models import LLMConfig, LLMRequestLog
from llm_connector.serializers import LLMConfigSerializer, LLMRequestLogSerializer


class LLMConfigViewSet(viewsets.ModelViewSet):
    serializer_class = LLMConfigSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return LLMConfig.objects.filter(user=self.request.user)


class LLMRequestLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = LLMRequestLogSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return LLMRequestLog.objects.filter(user=self.request.user)
