from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from spa.portfolio import landing_context


def landing(request):
    # The site root is public + server-rendered (SEO front door / link-tree). The SPA
    # owns /me and /portfolio/* (dev: Vite; prod: nginx routes / here, the rest to the SPA).
    return render(request, "spa/landing.html", landing_context())


def health(request):
    return JsonResponse({"message": "I am alive!"})


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
    path("", landing, name="index"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
