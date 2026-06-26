# [backend] Generation async plumbing

> Guide 1 of 3 for **roadmap #1** (wire CV + cover-letter pipeline to the frontend).
> Branch: `backend/generation-async-plumbing`. Siblings: `[backend]-generation-pipeline.md`,
> `[frontend]-tailored-render.md`.

## Context / goal

The CV filter and cover-letter builder only run via management commands today — nothing is exposed
over HTTP, nothing is persisted. We're adding **async, WebSocket-driven generation**: the SPA
creates a run over REST, a Celery worker executes it and streams progress over a Channels
WebSocket, and a single REST `GET` rehydrates state on refresh.

This guide stands up that loop **end-to-end with a stub task** — no pipeline logic yet. The point
is to prove Redis + Celery + Channels + WS auth + ownership all work before piling the (slow,
external) LLM pipeline on top (that's guide 2). When this guide is green you can create a run in the
browser/devtools, watch fake `pending → running → done` progress arrive over the socket, and `GET`
the snapshot.

The infra is **already provisioned in `lukehirsch/settings.py`** but unwired:
- `daphne` (first) + `channels` in `INSTALLED_APPS`; `ASGI_APPLICATION = "lukehirsch.asgi.application"`.
- `CHANNEL_LAYERS` → `channels_redis` at `REDIS_URL`.
- Full Celery config (`CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` = Redis, `CELERY_TASK_TIME_LIMIT`
  = 30 min).

What's missing: the Celery app bootstrap, a WebSocket-aware `asgi.py`, the `GenerationRun` model,
a consumer + routing, a stub task, and the REST viewset. The app is served by **daphne**, which
handles HTTP **and** WebSocket from the one ASGI app. The channel layer + Celery broker use Redis
even in `DEBUG` (only the Django cache is LocMem in dev), so the live pipe works locally.

## Affected files

| File | Change |
| --- | --- |
| `backend/lukehirsch/celery.py` | **new** — Celery app bootstrap |
| `backend/lukehirsch/__init__.py` | edit — export `celery_app` so `@shared_task` autodiscovery + `-A lukehirsch` works |
| `backend/lukehirsch/asgi.py` | edit — `ProtocolTypeRouter` with the WebSocket stack |
| `backend/jac/models.py` | edit — add `GenerationRun` model |
| `backend/jac/migrations/00XX_generationrun.py` | **new** — `makemigrations jac` |
| `backend/jac/tasks.py` | **new** — `generate_run` Celery task (STUB body in this guide) + `publish_event` helper |
| `backend/jac/consumers.py` | **new** — `GenerationConsumer` |
| `backend/jac/ws_routing.py` | **new** — websocket URL patterns |
| `backend/jac/serializers.py` | edit — `GenerationRunCreateSerializer` + `GenerationRunSerializer` |
| `backend/jac/views.py` | edit — `GenerationRunViewSet` |
| `backend/jac/urls.py` | edit — register `generations` |

## The code

### 1. `backend/lukehirsch/celery.py` (new)

```python
"""Celery application bootstrap.

Settings live in Django (`CELERY_*` keys in settings.py); this just builds the app and turns on
task autodiscovery so every app's `tasks.py` is picked up. Imported from `lukehirsch/__init__.py`
so `@shared_task` binds to this app and `celery -A lukehirsch worker` resolves it.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lukehirsch.settings")

app = Celery("lukehirsch")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
```

### 2. `backend/lukehirsch/__init__.py` (edit — currently empty)

```python
from .celery import app as celery_app

__all__ = ("celery_app",)
```

### 3. `backend/lukehirsch/asgi.py` (edit)

Build the HTTP app **first** (it triggers `django.setup()` / app loading) and only then import the
WS routing — `jac.ws_routing` pulls in consumers → models, which must not be imported before apps
are ready.

```python
"""ASGI config: HTTP via Django, WebSocket via Channels."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lukehirsch.settings")

# Must run before importing anything that touches the app registry (consumers -> models).
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from jac.ws_routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
        ),
    }
)
```

### 4. `backend/jac/models.py` — add `GenerationRun` (after `JobPostAddress`)

```python
class GenerationRun(models.Model):
    """One async CV + cover-letter generation. The view persists it `pending` and enqueues the
    Celery task (`jac.tasks.generate_run`), which streams progress over the `gen_<pk>` channel
    group and writes the final `result`. The SPA subscribes by WebSocket; a REST GET rehydrates
    the snapshot on refresh.
    """

    class Status(models.TextChoices):
        pending = "pending", _("Pending")
        running = "running", _("Running")
        done = "done", _("Done")
        failed = "failed", _("Failed")

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="generation_runs"
    )
    job_posting = models.ForeignKey(
        JobPosting, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="generation_runs",
    )

    # Inputs (mirror the cover_letter command's knobs).
    posting_text = models.TextField()
    grade = models.CharField(max_length=12, blank=True)  # "" => auto-detect from the alias
    alias = models.CharField(max_length=100, default="default")
    verify_grounding = models.BooleanField(default=False)
    verifier_alias = models.CharField(max_length=100, blank=True)
    personal_paragraph = models.BooleanField(default=False)
    research_alias = models.CharField(max_length=100, blank=True)
    max_body_snippets = models.PositiveSmallIntegerField(default=4)
    # CV scoping (all optional; map onto CV.__init__).
    domains = models.JSONField(default=list, blank=True)  # list[str] domain names
    started = models.DateField(null=True, blank=True)
    ended = models.DateField(null=True, blank=True)
    min_skill_proficiency = models.CharField(max_length=12, blank=True)

    # Lifecycle.
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.pending
    )
    stage = models.CharField(max_length=80, blank=True)  # last human-readable progress label
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"GenerationRun {self.pk} ({self.status})"
```

`_` is already imported in `models.py` (used by the choices above); if not, add
`from django.utils.translation import gettext_lazy as _`.

Then: `python manage.py makemigrations jac`.

### 5. `backend/jac/tasks.py` (new — STUB body)

```python
"""Celery tasks for async generation.

`generate_run` owns the run lifecycle and streams progress to the `gen_<run_id>` channel group;
the WebSocket consumer forwards those to the browser. THIS GUIDE ships a stub body — guide 2 swaps
it for the real CV + cover-letter pipeline. The lifecycle / event contract here is the part the
consumer + frontend depend on, so keep it stable.
"""

import logging
import time

from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer

from jac.models import GenerationRun

logger = logging.getLogger(__name__)


def publish_event(run_id: int, payload: dict) -> None:
    """Fan a progress/terminal event out to the run's channel group. No-op if no layer."""
    layer = get_channel_layer()
    if layer is None:
        return
    async_to_sync(layer.group_send)(
        f"gen_{run_id}", {"type": "gen.event", "payload": payload}
    )


def _progress(run: GenerationRun, stage: str) -> None:
    run.stage = stage
    run.save(update_fields=["stage", "updated_at"])
    publish_event(run.pk, {"event": "progress", "status": run.status, "stage": stage})


@shared_task
def generate_run(run_id: int) -> None:
    run = GenerationRun.objects.filter(pk=run_id).first()
    if run is None:
        logger.warning("generate_run: no run %s", run_id)
        return

    run.status = GenerationRun.Status.running
    run.save(update_fields=["status", "updated_at"])
    publish_event(run.pk, {"event": "progress", "status": run.status, "stage": ""})

    try:
        # --- STUB pipeline (replaced in guide 2) ---
        for stage in ("filtering CV", "writing letter"):
            _progress(run, stage)
            time.sleep(0.2)
        run.result = {"meta": {"stub": True}, "cv": {}, "cover_letter": {}}
        run.status = GenerationRun.Status.done
        run.stage = "done"
        run.save(update_fields=["result", "status", "stage", "updated_at"])
        publish_event(
            run.pk,
            {"event": "done", "status": run.status, "result": run.result},
        )
    except Exception as exc:  # noqa: BLE001 — surface any pipeline failure to the client
        logger.exception("generate_run %s failed", run_id)
        run.status = GenerationRun.Status.failed
        run.error = str(exc)
        run.save(update_fields=["status", "error", "updated_at"])
        publish_event(
            run.pk, {"event": "failed", "status": run.status, "error": run.error}
        )
```

### 6. `backend/jac/consumers.py` (new)

```python
"""WebSocket consumer for streaming a GenerationRun's progress.

Auth comes from AuthMiddlewareStack (the SPA's Django session cookie rides the WS handshake).
On connect we verify ownership, join the `gen_<pk>` group, and immediately push the current
snapshot so a late/reconnecting client (page refresh mid-run) isn't stuck waiting for the next
event. The task fans events into the group via `jac.tasks.publish_event`.
"""

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from jac.models import GenerationRun


class GenerationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.pk = int(self.scope["url_route"]["kwargs"]["pk"])
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return
        snapshot = await self._snapshot(user.id, self.pk)
        if snapshot is None:
            await self.close(code=4404)
            return
        self.group = f"gen_{self.pk}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self.send_json({"event": "snapshot", **snapshot})

    async def disconnect(self, code):
        group = getattr(self, "group", None)
        if group is not None:
            await self.channel_layer.group_discard(group, self.channel_name)

    async def gen_event(self, message):
        """Forward a group event (sent with type "gen.event") to the client."""
        await self.send_json(message["payload"])

    @database_sync_to_async
    def _snapshot(self, user_id: int, pk: int):
        run = GenerationRun.objects.filter(pk=pk, user_id=user_id).first()
        if run is None:
            return None
        return {
            "status": run.status,
            "stage": run.stage,
            "result": run.result,
            "error": run.error,
        }
```

### 7. `backend/jac/ws_routing.py` (new)

```python
from django.urls import re_path

from jac.consumers import GenerationConsumer

websocket_urlpatterns = [
    re_path(r"^ws/jac/generations/(?P<pk>\d+)/$", GenerationConsumer.as_asgi()),
]
```

### 8. `backend/jac/serializers.py` — add (read the file header first to match imports/style)

```python
class GenerationRunCreateSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = GenerationRun
        fields = [
            "user", "posting_text", "grade", "alias",
            "verify_grounding", "verifier_alias",
            "personal_paragraph", "research_alias", "max_body_snippets",
            "domains", "started", "ended", "min_skill_proficiency",
        ]

    def validate_grade(self, value):
        if value and value not in ("light", "standard", "strong"):
            raise serializers.ValidationError("Unknown grade.")
        return value


class GenerationRunSerializer(serializers.ModelSerializer):
    job_posting_title = serializers.CharField(
        source="job_posting.title", read_only=True, default=""
    )

    class Meta:
        model = GenerationRun
        fields = [
            "id", "status", "stage", "error", "result",
            "grade", "alias", "personal_paragraph", "verify_grounding",
            "job_posting_title", "created_at", "updated_at",
        ]
        read_only_fields = fields
```

Import `GenerationRun` at the top of `serializers.py`.

### 9. `backend/jac/views.py` — add `GenerationRunViewSet`

```python
from rest_framework import mixins

from jac.models import GenerationRun, JobPosting          # extend the existing model import
from jac.serializers import (                              # extend the existing serializer import
    GenerationRunCreateSerializer,
    GenerationRunSerializer,
)
from jac.tasks import generate_run


class GenerationRunViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """Create + read async generation runs. Create persists a JobPosting + a pending run and
    enqueues the Celery task; the SPA then streams progress over the WebSocket. Retrieve is the
    snapshot used to rehydrate after a refresh."""

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return GenerationRun.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        if self.action == "create":
            return GenerationRunCreateSerializer
        return GenerationRunSerializer

    def perform_create(self, serializer):
        run = serializer.save()
        jp = JobPosting.objects.create(
            user=self.request.user,
            posting_text=run.posting_text,
            language="en",  # refined by AddressExtract in guide 2
        )
        run.job_posting = jp
        run.save(update_fields=["job_posting", "updated_at"])
        generate_run.delay(run.pk)
        self._created = run

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        self.perform_create(ser)
        out = GenerationRunSerializer(self._created)
        return Response(out.data, status=status.HTTP_201_CREATED)
```

(`status`, `Response`, `viewsets`, `IsAuthenticated` are already imported in `views.py`.)

### 10. `backend/jac/urls.py` — register the viewset

```python
from jac.views import (..., GenerationRunViewSet)   # add to the existing import block

router.register("generations", GenerationRunViewSet, basename="generation")
```

## Tests (already written, start red)

- `backend/jac/tests/test_generation_api.py` — `GenerationRun` model default status; viewset
  `create` persists a run + `JobPosting` and enqueues (`generate_run.delay` patched), returns the
  run id with status `pending`; `retrieve` returns the snapshot/result; user-scoping (B can't read
  A's run); grade validation rejects junk.
- `backend/jac/tests/test_generation_ws.py` — `WebsocketCommunicator` against `GenerationConsumer`
  under an `InMemoryChannelLayer` override: anonymous → rejected (no accept); non-owner → rejected;
  owner → connects, receives the initial `snapshot`, then a `publish_event` reaches the client.

Run: `cd backend && python manage.py test jac.tests.test_generation_api jac.tests.test_generation_ws`

> The task-body test (`test_generation_task.py`) lands with guide 2, since it asserts the real
> result shape. In this guide the stub task is exercised indirectly by the WS test's event flow.

## Verification (human)

1. `pip` deps already present (`celery`, `channels`, `channels_redis`, `daphne`, `redis`).
2. `makemigrations jac` && `migrate`.
3. Start Redis (`redis-server`), then the worker: `cd backend && celery -A lukehirsch worker -l info`.
   You should see `generate_run` in the registered-tasks list.
4. Serve ASGI: `daphne lukehirsch.asgi:application` (or `runserver`, which uses the ASGI app since
   `daphne` is installed). Log in via the SPA so you have a session cookie.
5. In devtools console (same origin):
   ```js
   const r = await fetch("/api/jac/generations/", {
     method: "POST",
     headers: { "Content-Type": "application/json",
       "X-CSRFToken": document.cookie.match(/csrftoken=([^;]+)/)[1] },
     body: JSON.stringify({ posting_text: "We need a backend dev.", alias: "default" }),
   }).then(r => r.json());
   const ws = new WebSocket(`ws://${location.host}/ws/jac/generations/${r.id}/`);
   ws.onmessage = (e) => console.log(JSON.parse(e.data));
   ```
   Expect: a `snapshot` event, then `progress` (filtering CV / writing letter), then `done` with a
   stub `result`.
6. `GET /api/jac/generations/<id>/` returns the final snapshot (status `done`).
7. Done = events stream over the socket, ownership is enforced (try another user's id → socket
   closes), and the REST snapshot matches.
