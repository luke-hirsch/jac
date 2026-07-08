"""Channels consumer for async generation: session-auth + ownership gate, then forwards
`gen_<pk>` group events to the browser and pushes a snapshot on connect."""

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from jac.models import GenerationRun


class GenerationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.pk = int(self.scope["url_route"]["kwargs"]["pk"])  # type: ignore
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)
            return
        if not await self._owns(user.id, self.pk):
            await self.close(code=4404)
            return
        self.group = f"gen_{self.pk}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        # Read AFTER joining the group: any event during accept() is then reconciled by this
        # baseline snapshot, closing the read-then-subscribe race.
        snapshot = await self._snapshot(self.pk)
        await self.send_json({"event": "snapshot", **snapshot})

    async def disconnect(self, code):
        group = getattr(self, "group", None)
        if group is not None:
            await self.channel_layer.group_discard(group, self.channel_name)

    async def gen_event(self, message):
        """Forward a group event (sent with type "gen.event") to the client."""
        await self.send_json(message["payload"])

    @database_sync_to_async
    def _owns(self, user_id: int, pk: int) -> bool:
        return GenerationRun.objects.filter(
            pk=pk, job_application__user_id=user_id
        ).exists()

    @database_sync_to_async
    def _snapshot(self, pk: int) -> dict:
        run = GenerationRun.objects.filter(pk=pk).first()
        if run is None:  # deleted between the ownership check and here
            return {"status": "failed", "stage": "", "result": None, "error": "gone"}
        return {
            "status": run.status,
            "stage": run.stage,
            "result": run.result,
            "error": run.error,
        }
