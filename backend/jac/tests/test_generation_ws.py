"""Async generation — WebSocket consumer (auth, ownership, snapshot, live events).

Red until `[backend]-generation-async-plumbing` lands `GenerationConsumer`, `ws_routing`, and
`jac.tasks.publish_event`. Runs under an in-memory channel layer so no Redis is needed; routes
through `URLRouter` so `scope["url_route"]` is populated, and sets `scope["user"]` directly
(standing in for AuthMiddlewareStack).
"""

from unittest.mock import patch

from asgiref.sync import async_to_sync, sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser, User
from django.test import TransactionTestCase, override_settings

from jac.models import GenerationRun
from jac.tasks import publish_event
from jac.ws_routing import websocket_urlpatterns

from ._helpers import _application

IN_MEMORY = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


@override_settings(CHANNEL_LAYERS=IN_MEMORY)
class GenerationConsumerTests(TransactionTestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="ws_alice", password="pass")
        self.bob = User.objects.create_user(username="ws_bob", password="pass")
        self.gen = GenerationRun.objects.create(
            job_application=_application(self.alice, posting_text="x")
        )

    def _communicator(self, pk, user):
        comm = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns), f"/ws/jac/generations/{pk}/"
        )
        comm.scope["user"] = user
        return comm

    def test_anonymous_is_rejected(self):
        async def run():
            comm = self._communicator(self.gen.pk, AnonymousUser())
            connected, _ = await comm.connect()
            self.assertFalse(connected)

        async_to_sync(run)()

    def test_non_owner_is_rejected(self):
        async def run():
            comm = self._communicator(self.gen.pk, self.bob)
            connected, _ = await comm.connect()
            self.assertFalse(connected)

        async_to_sync(run)()

    def test_owner_gets_snapshot_then_live_event(self):
        async def run():
            comm = self._communicator(self.gen.pk, self.alice)
            connected, _ = await comm.connect()
            self.assertTrue(connected)

            snapshot = await comm.receive_json_from()
            self.assertEqual(snapshot["event"], "snapshot")
            self.assertEqual(snapshot["status"], "pending")

            # publish_event is sync (async_to_sync inside) — run it off the loop thread.
            await sync_to_async(publish_event)(
                self.gen.pk, {"event": "progress", "status": "running", "stage": "filtering CV"}
            )
            event = await comm.receive_json_from()
            self.assertEqual(event["event"], "progress")
            self.assertEqual(event["stage"], "filtering CV")
            await comm.disconnect()

        async_to_sync(run)()

    def test_subscribes_before_reading_snapshot(self):
        """`[backend]-correctness-bugs`: the consumer must join the gen_<pk> group BEFORE reading
        the snapshot, so an event fired during connect can't be lost. White-box ordering assertion —
        the race itself is timing-dependent and can't be made cleanly black-box red.

        Red today twice over: current connect() reads the snapshot first (wrong order), and calls
        `_snapshot(user_id, pk)` (two args) — the fixed signature is `_snapshot(pk)`."""
        from channels.layers import InMemoryChannelLayer

        from jac.consumers import GenerationConsumer

        order: list[str] = []
        orig_group_add = InMemoryChannelLayer.group_add

        async def spy_group_add(self, group, channel):
            order.append("group_add")
            return await orig_group_add(self, group, channel)

        async def spy_snapshot(self, pk):
            order.append("snapshot")
            return {"status": "pending", "stage": "", "result": None, "error": ""}

        async def run():
            with patch.object(InMemoryChannelLayer, "group_add", spy_group_add), patch.object(
                GenerationConsumer, "_snapshot", spy_snapshot
            ):
                comm = self._communicator(self.gen.pk, self.alice)
                connected, _ = await comm.connect()
                self.assertTrue(connected)
                await comm.receive_json_from()
                await comm.disconnect()

        async_to_sync(run)()
        self.assertLess(order.index("group_add"), order.index("snapshot"))
