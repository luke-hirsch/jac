"""URL routing for the LLM connector.

Mounted by the project at the prefix defined in `lukehirsch/urls.py`.
Explicit `basename` is required because both viewsets override
`get_queryset` rather than declaring a class-level `queryset`.
"""

from django.urls import path
from rest_framework.routers import DefaultRouter

from llm_connector.views import ExecutorListView, LLMConfigViewSet, LLMRequestLogViewSet

router = DefaultRouter()
router.register("configs", LLMConfigViewSet, basename="llmconfig")
router.register("request-logs", LLMRequestLogViewSet, basename="llmrequestlog")

urlpatterns = [
    path("executors/", ExecutorListView.as_view(), name="llmexecutor-list"),
    *router.urls,
]
