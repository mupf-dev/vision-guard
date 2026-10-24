"""go2rtc-HTTP-API: Stream-Synchronisation, Standbilder, URL-Probe."""
import asyncio
import logging
import secrets
import struct
from typing import Any, Optional

import httpx

from .config import config

log = logging.getLogger("vg.go2rtc")

PREFIX = "cam_"


def stream_name(camera_id: int) -> str:
    return f"{PREFIX}{camera_id}"


def detect_stream_name(camera_id: int) -> str:
    return f"{PREFIX}{camera_id}_detect"


def rtsp_url(name: str) -> str:
    return f"{config.go2rtc_rtsp}/{name}"


def desired_streams(cameras: list[Any]) -> dict[str, str]:
    """Soll-Zustand: Streamname -> Quell-URL. Alle Kameras (auch deaktivierte, für Live-Ansicht)."""
    out: dict[str, str] = {}
    for c in cameras:
        out[stream_name(c.id)] = c.url
        if c.detect_url:
            out[detect_stream_name(c.id)] = c.detect_url
    return out


def _is_managed(name: str) -> bool:
    if not name.startswith(PREFIX):
        return False
    rest = name[len(PREFIX):]
    if rest.endswith("_detect"):
        rest = rest[: -len("_detect")]
    return rest.isdigit()


def _stream_srcs(info: Any) -> list[str]:
    """Quell-URLs eines Streams aus GET /api/streams."""
    srcs: list[str] = []
    if isinstance(info, dict):
        for p in info.get("producers") or []:
            if isinstance(p, dict) and p.get("url"):
                srcs.append(p["url"])
    return srcs


async def list_streams(client: httpx.AsyncClient) -> dict[str, Any]:
    r = await client.get(f"{config.go2rtc_url}/api/streams")
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, dict) else {}


async def put_stream(client: httpx.AsyncClient, name: str, src: str) -> None:
    r = await client.put(f"{config.go2rtc_url}/api/streams", params={"name": name, "src": src})
    if r.status_code >= 400:
        # Ältere/neuere Versionen: PATCH aktualisiert bestehende Streams
        r2 = await client.patch(f"{config.go2rtc_url}/api/streams", params={"name": name, "src": src})
        r2.raise_for_status()


async def delete_stream(client: httpx.AsyncClient, name: str) -> None:
    r = await client.delete(f"{config.go2rtc_url}/api/streams", params={"src": name})
    if r.status_code >= 400 and r.status_code != 404:
        r.raise_for_status()


async def sync(cameras: list[Any]) -> bool:
    """Bringt go2rtc auf den Soll-Zustand. Gibt True bei Erfolg zurück."""
    want = desired_streams(cameras)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            have = await list_streams(client)
            for name, info in have.items():
                if _is_managed(name) and name not in want:
                    log.info("go2rtc: entferne Stream %s", name)
                    await delete_stream(client, name)
            for name, src in want.items():
                if name in have and src in _stream_srcs(have[name]):
                    continue
                if name in have:
                    await delete_stream(client, name)
                log.info("go2rtc: setze Stream %s", name)
                await put_stream(client, name, src)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("go2rtc-Sync fehlgeschlagen: %s", e)
        return False


async def wait_ready(timeout: float = 120) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    async with httpx.AsyncClient(timeout=3) as client:
        while loop.time() < deadline:
            try:
                r = await client.get(f"{config.go2rtc_url}/api/streams")
                if r.status_code < 500:
                    return True
            except Exception:
                pass
            await asyncio.sleep(2)
    return False


async def fetch_frame(src: str, timeout: float = 10) -> bytes:
    """JPEG-Standbild eines go2rtc-Streams. Wirft bei Fehlern."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(f"{config.go2rtc_url}/api/frame.jpeg", params={"src": src})
        if r.status_code != 200 or not r.content.startswith(b"\xff\xd8"):
            raise RuntimeError(f"go2rtc frame: HTTP {r.status_code} {r.text[:200] if r.status_code != 200 else ''}".strip())
        return r.content


def jpeg_size(data: bytes) -> Optional[tuple[int, int]]:
    """(Breite, Höhe) aus einem JPEG (SOF-Marker)."""
    i = 2
    n = len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + seglen
    return None


def _codec_from_info(info: Any) -> Optional[str]:
    if not isinstance(info, dict):
        return None
    for p in info.get("producers") or []:
        for m in (p or {}).get("medias") or []:
            # z. B. "video, recvonly, H264" oder "video, recvonly, H265, JPEG"
            parts = [x.strip() for x in str(m).split(",")]
            if parts and parts[0] == "video" and len(parts) >= 3:
                return parts[2].upper()
        # neuere Versionen: receivers mit codec-Objekten
        for rcv in (p or {}).get("receivers") or []:
            codec = (rcv or {}).get("codec") or {}
            name = codec.get("codec_name") or codec.get("name")
            if name and str(codec.get("codec_type", "video")) == "video":
                return str(name).upper()
    return None


async def probe(url: str, timeout: float = 20) -> dict[str, Any]:
    """Prüft eine Kamera-URL über einen temporären go2rtc-Stream."""
    name = f"probe_{secrets.token_hex(6)}"
    result: dict[str, Any] = {"ok": False, "width": None, "height": None, "codec": None, "error": None}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5)) as client:
            await put_stream(client, name, url)
            try:
                frame_task = asyncio.create_task(
                    client.get(f"{config.go2rtc_url}/api/frame.jpeg", params={"src": name}))
                # Codec-Infos liefert go2rtc nur, solange ein Consumer verbunden ist → parallel abfragen
                while not frame_task.done() and result["codec"] is None:
                    await asyncio.sleep(0.25)
                    try:
                        info = (await client.get(f"{config.go2rtc_url}/api/streams", params={"src": name})).json()
                        result["codec"] = _codec_from_info(info)
                    except Exception:
                        pass
                r = await frame_task
                if r.status_code == 200 and r.content.startswith(b"\xff\xd8"):
                    size = jpeg_size(r.content)
                    if size:
                        result["width"], result["height"] = size
                    result["ok"] = True
                else:
                    msg = r.text.strip()[:300] if r.status_code != 200 else "Kein Bild erhalten"
                    result["error"] = msg or f"HTTP {r.status_code}"
            finally:
                try:
                    await delete_stream(client, name)
                except Exception:
                    pass
    except httpx.TimeoutException:
        result["error"] = "Zeitüberschreitung beim Verbindungsaufbau"
        await _cleanup(name)
    except Exception as e:  # noqa: BLE001
        result["error"] = str(e) or e.__class__.__name__
        await _cleanup(name)
    return result


async def _cleanup(name: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await delete_stream(client, name)
    except Exception:
        pass
