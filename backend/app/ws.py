"""WebSocket-Verteiler. broadcast() ist thread-sicher (MQTT-Thread, Threadpool, Event-Loop)."""
import asyncio
import json
import logging
import threading
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("vg.ws")


class Hub:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: set[asyncio.Queue] = set()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def _put_all(self, text: str) -> None:
        for q in list(self.clients):
            try:
                q.put_nowait(text)
            except asyncio.QueueFull:
                pass  # langsamer Client: Nachricht verwerfen

    def broadcast(self, msg: dict[str, Any]) -> None:
        if not self.loop or not self.clients:
            return
        try:
            text = json.dumps(msg, default=str, separators=(",", ":"))
        except Exception:
            log.exception("WS-Nachricht nicht serialisierbar")
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self._put_all(text)
        else:
            try:
                self.loop.call_soon_threadsafe(self._put_all, text)
            except RuntimeError:
                pass  # Loop beendet

    async def serve(self, ws: WebSocket, initial: list[dict[str, Any]]) -> None:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        for m in initial:
            q.put_nowait(json.dumps(m, default=str))
        self.clients.add(q)

        async def sender() -> None:
            while True:
                text = await q.get()
                await ws.send_text(text)

        send_task = asyncio.create_task(sender())
        try:
            while True:
                # Client-Nachrichten (z. B. Pings) werden ignoriert
                recv = asyncio.create_task(ws.receive_text())
                done, _ = await asyncio.wait({recv, send_task}, return_when=asyncio.FIRST_COMPLETED)
                if send_task in done:
                    recv.cancel()
                    break
                recv.result()
        except Exception:
            pass
        finally:
            self.clients.discard(q)
            send_task.cancel()


hub = Hub()
