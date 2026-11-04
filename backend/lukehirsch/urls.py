from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from jac.queues import queue_report
from spa.legal import legal_page
from spa.portfolio import landing_context


def landing(request):
    return render(request, "spa/landing.html", landing_context(request))


def health(request):
    # Liveness only: no database or Valkey ping, or a hiccup there reads as "app is down".
    # X-Release: a deploy waits for its own commit to show up here.
    response = JsonResponse({"message": "I am alive!"})
    response["X-Release"] = settings.RELEASE
    return response


def health_queues(request):
    # Reached over the container network only: nginx doesn't route it on a public host.
    return JsonResponse(queue_report())


urlpatterns = [
    path("admin/", admin.site.urls),
    path("_allauth/", include("allauth.headless.urls")),
    path("api/jac/", include("jac.urls")),
    path("api/llm/", include("llm_connector.urls")),
    path("api/spa/", include("spa.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("health/", health, name="health"),
    path("health/queues", health_queues, name="health-queues"),
    path("legal/<slug:page>/", legal_page, name="legal"),
    path("", landing, name="index"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
