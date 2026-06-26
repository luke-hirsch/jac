"""Celery tasks for async generation. (stub)"""

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
