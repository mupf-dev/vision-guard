"""Embedding-Anfragen an den Worker (W2, K2) – `vg/internal/embed` → `vg/internal/embed_result`.

Der Worker rechnet mit dem geteilten DINOv2 der Tiererkennung (384-d, L2-normiert). Vertrag: CONTRACT.md
„Nachträge: Welle 2“, W2.E. Nutzer: B4 (Upload/Inventar-Crops), später A3-Referenzen, C4-Füllstand, B1-Text.

    from .embed_client import EmbedUnavailable, EmbedError, request
    res = request("item", path="identities/31/upload_1.jpg", bbox=None, augment=True)   # blockiert ≤ timeout
    res.embedding  # list[float] (384)

Blockierend und thread-sicher; aus async-Code per `run_in_threadpool` aufrufen. Keine Antwort binnen `timeout`
→ `EmbedUnavailable` (Router: 503 „Analyse-Dienst nicht erreichbar“), `ok:false` → `EmbedError` (Router: 422).
"""
from __future__ import annotations

import base64
import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("vg.embed")

TOPIC = "vg/internal/embed"
MAX_IMAGE_BYTES = 4 * 1024 * 1024


class EmbedUnavailable(Exception):
    """Worker hat nicht rechtzeitig geantwortet (offline, Modell lädt noch, Warteschlange voll …)."""


class EmbedError(Exception):
    """Worker hat geantwortet, konnte aber nicht rechnen (Bild fehlt/kaputt, Art nicht unterstützt …)."""


@dataclass
class EmbedResult:
    embedding: list[float]
    dim: int
    model: str = ""
    variants: Optional[list[list[float]]] = None
    width: Optional[int] = None
    height: Optional[int] = None
    ms: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


class _Pending:
    __slots__ = ("event", "result")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Optional[dict[str, Any]] = None


_lock = threading.Lock()
_pending: dict[str, _Pending] = {}


def handle_result(camera_id: Optional[int], msg: dict[str, Any]) -> None:
    """`vg/internal/embed_result` (direkt im MQTT-Thread, siehe mqtt_bus.register_handler)."""
    rid = str(msg.get("request_id") or "")
    with _lock:
        p = _pending.get(rid)
    if p is None:
        return   # zu spät oder fremde Anfrage
    p.result = msg
    p.event.set()


def request(kind: str, *, path: Optional[str] = None, image_bytes: Optional[bytes] = None,
            bbox: Optional[list[float]] = None, augment: bool = False, text: Optional[str] = None,
            model: Optional[str] = None, timeout: float = 15.0) -> EmbedResult:
    """W3: `model` = "dinov2" (Default für item/image) | "siglip2" (Default für text, B1 Zeitmaschine)."""
    if kind not in ("item", "image", "text"):
        raise ValueError(f"unbekannte Embedding-Art: {kind}")
    if model not in (None, "dinov2", "siglip2"):
        raise ValueError(f"unbekanntes Embedding-Modell: {model}")
    if kind == "text" and not (text or "").strip():
        raise ValueError("text nötig")
    if kind != "text" and not path and not image_bytes:
        raise ValueError("path oder image_bytes nötig")
    if image_bytes is not None and len(image_bytes) > MAX_IMAGE_BYTES:
        raise EmbedError("Bild zu groß (max. 4 MB)")
    from .mqtt_bus import bus   # spät importieren (Zyklus mqtt_bus → events → …)

    if bus.client is None or not bus.client.is_connected():
        raise EmbedUnavailable("Analyse-Dienst nicht erreichbar")
    rid = uuid.uuid4().hex
    body: dict[str, Any] = {"request_id": rid, "kind": kind, "path": path, "bbox": bbox, "augment": bool(augment),
                            "text": text, "model": model or ("siglip2" if kind == "text" else "dinov2"),
                            "image_b64": base64.b64encode(image_bytes).decode() if image_bytes else None}
    p = _Pending()
    with _lock:
        _pending[rid] = p
    try:
        bus.publish(TOPIC, body, qos=1)
        if not p.event.wait(timeout):
            log.warning("Embedding %s: keine Antwort des Workers binnen %.0f s", kind, timeout)
            raise EmbedUnavailable("Analyse-Dienst nicht erreichbar")
    finally:
        with _lock:
            _pending.pop(rid, None)
    msg = p.result or {}
    if not msg.get("ok") or not msg.get("embedding"):
        err = str(msg.get("error") or "unbekannter Fehler")
        if err == "busy":
            raise EmbedUnavailable("Analyse-Dienst ausgelastet – bitte gleich noch einmal versuchen")
        raise EmbedError(err)
    emb = [float(x) for x in msg["embedding"]]
    return EmbedResult(embedding=emb, dim=len(emb), model=str(msg.get("model") or ""),
                       variants=msg.get("variants"), width=msg.get("width"), height=msg.get("height"),
                       ms=float(msg.get("ms") or 0), raw=msg)
