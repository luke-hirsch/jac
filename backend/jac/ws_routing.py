from django.urls import re_path

from jac.consumers import GenerationConsumer

websocket_urlpatterns = [
    re_path(r"^ws/jac/generations/(?P<pk>\d+)/$", GenerationConsumer.as_asgi()),  # type: ignore
]
