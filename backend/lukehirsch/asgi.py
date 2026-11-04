"""
ASGI config for lukehirsch project.
"""

import os

from django.core.asgi import get_asgi_application

# Must precede get_asgi_application(): it runs django.setup(), which needs the settings module.
# `runserver` sets it for you; `daphne lukehirsch.asgi:application` in the container does not.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lukehirsch.settings")
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator

from jac.ws_routing import websocket_urlpatterns

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
        ),
    }
)
