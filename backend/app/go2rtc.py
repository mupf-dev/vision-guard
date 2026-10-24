"""go2rtc-HTTP-API: Stream-Synchronisation, Standbilder, URL-Probe."""
import asyncio
import logging
import secrets
import struct
from typing import Any, Optional

import httpx

from . import youtube
from .config import config

log = logging.getLogger("vg.go2rtc")

PREFIX = "cam_"
ALIAS = "alias:"   # interner Marker in desired_streams: Stream ist Alias eines anderen go2rtc-Streams

# Zuletzt gesetzte Quelle je Stream: name -> (Kamera-URL, go2rtc-Quelle).
# Nötig, weil go2rtc für laufende ffmpeg-Quellen nicht die `ffmpeg:`-Quelle, sondern seine interne
# RTSP-Adresse meldet – ohne dieses Gedächtnis würde jeder Sync YouTube-Streams ersetzen.
_applied: dict[str, tuple[str, str]] = {}


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
            if youtube.is_youtube(c.detect_url) and c.detect_url.strip() == (c.url or "").strip():
                # Gleicher YouTube-Link: kein zweiter Abruf, Detect-Stream ist ein Alias des Hauptstreams
                # (halbe Bandbreite, Analyse und Live-Bild auf derselben Zeitachse).
                out[detect_stream_name(c.id)] = ALIAS + stream_name(c.id)
            else:
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
    """Stream anlegen bzw. Quelle setzen – per PATCH (nur im Speicher von go2rtc).

    PUT würde die Quelle zusätzlich in go2rtc.yaml schreiben (inkl. Kamera-Zugangsdaten), daher nur als Fallback.
    PATCH auf einen bestehenden Stream tauscht nur die Quelle für den nächsten Verbindungsaufbau – laufende
    Zuschauer (Browser, Worker) bleiben verbunden. Ist `src` der Name eines Streams, wird `name` ein Alias darauf.
    """
    r = await client.patch(f"{config.go2rtc_url}/api/streams", params={"name": name, "src": src})
    if r.status_code >= 400:
        r2 = await client.put(f"{config.go2rtc_url}/api/streams", params={"name": name, "src": src})
        r2.raise_for_status()


async def delete_stream(client: httpx.AsyncClient, name: str) -> None:
    r = await client.delete(f"{config.go2rtc_url}/api/streams", params={"src": name})
    if r.status_code >= 400 and r.status_code != 404:
        r.raise_for_status()


async def source_for(url: str, height: int = youtube.MAIN_HEIGHT) -> Optional[str]:
    """Quelle für go2rtc; YouTube-Links werden zur aktuellen HLS-Adresse aufgelöst (None = derzeit nicht verfügbar)."""
    if youtube.is_youtube(url):
        return await youtube.resolve(url, height)
    return url


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
                    _applied.pop(name, None)
            for name in list(_applied):
                if name not in have:
                    _applied.pop(name)  # go2rtc neu gestartet: Stream fehlt -> neu anlegen
            created: set[str] = set()   # in diesem Durchlauf neu angelegte Streams
            for name, url in want.items():
                if url.startswith(ALIAS):
                    await _sync_alias(client, name, url[len(ALIAS):], have, created)
                    continue
                height = youtube.DETECT_HEIGHT if name.endswith("_detect") else youtube.MAIN_HEIGHT
                src = await source_for(url, height)
                if src is None:
                    continue  # YouTube gerade nicht erreichbar: bestehenden Stream behalten, später erneut
                if name in have and (_applied.get(name) == (url, src) or src in _stream_srcs(have[name])):
                    _applied[name] = (url, src)
                    continue
                prev = _applied.get(name)
                if name in have and (prev is None or prev[0] == url):
                    # Gleiche Kamera, nur neue Quelle (YouTube: neue HLS-Adresse) bzw. Backend-Neustart:
                    # Quelle per PATCH tauschen, laufende Zuschauer bleiben verbunden. Früher wurde jede
                    # ffmpeg-Quelle bei jedem Sync (30 s) gelöscht und neu angelegt, weil go2rtc für laufende
                    # ffmpeg-Quellen nur seine interne RTSP-Adresse meldet -> Live-Bild und Analyse rissen ab.
                    log.info("go2rtc: aktualisiere Quelle von %s", name)
                    await put_stream(client, name, src)
                else:
                    if name in have:
                        # Kamera-URL wurde geändert: neu anlegen, damit sofort die neue Quelle gilt
                        await delete_stream(client, name)
                    log.info("go2rtc: setze Stream %s", name)
                    await put_stream(client, name, src)
                    created.add(name)
                _applied[name] = (url, src)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("go2rtc-Sync fehlgeschlagen: %s", e)
        return False


async def _sync_alias(client: httpx.AsyncClient, name: str, target: str, have: dict[str, Any],
                      created: set[str]) -> None:
    """Verknüpft `name` in go2rtc mit dem Stream `target` (gleiche Quelle, keine zweite Verbindung).

    go2rtc: PATCH mit einem bestehenden Streamnamen als Quelle macht `name` zu einem Alias auf dasselbe
    Stream-Objekt. Wurde `target` neu angelegt (oder fehlt der Alias), muss neu verknüpft werden.
    """
    marker = ALIAS + target
    if target not in have and target not in created:
        return  # Ziel existiert (noch) nicht, z. B. YouTube nicht erreichbar -> nächster Sync
    if name in have and _applied.get(name) == (marker, marker) and target not in created:
        return
    if name in have:
        await delete_stream(client, name)   # alte (eigene) Quelle bzw. veralteter Alias
    log.info("go2rtc: verknüpfe Stream %s mit %s", name, target)
    await put_stream(client, name, target)
    _applied[name] = (marker, marker)


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
    src = await source_for(url)
    if src is None:
        result["error"] = "YouTube-Livestream nicht verfügbar (offline oder kein Livestream)"
        return result
    url = src
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
