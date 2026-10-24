"""YouTube-Livestreams als Kameraquelle.

yt-dlp ermittelt nur die aktuelle HLS-Adresse (`-g`), es wird nichts heruntergeladen. go2rtc liest den Stream
anschließend per ffmpeg live in den Speicher. HLS-Adressen von YouTube laufen nach einigen Stunden ab; sie werden
rechtzeitig neu ermittelt (go2rtc-Sync alle 30 s).
"""
import asyncio
import logging
import re
import time
from typing import Optional

log = logging.getLogger("vg.youtube")

_YT = re.compile(r"^https?://(www\.|m\.)?(youtube\.com|youtu\.be)/", re.I)
_EXPIRE = re.compile(r"/expire/(\d+)/")

MAIN_HEIGHT = 1080
DETECT_HEIGHT = 720
REFRESH_BEFORE_S = 30 * 60      # so lange vor Ablauf neu ermitteln
MAX_TTL_S = 4 * 3600
RETRY_S = 60                    # nach Fehler (z. B. Stream offline)

# (url, height) -> (go2rtc-Quelle oder None, gültig bis)
_cache: dict[tuple[str, int], tuple[Optional[str], float]] = {}
_lock = asyncio.Lock()


def is_youtube(url: Optional[str]) -> bool:
    return bool(url and _YT.match(url.strip()))


async def _run_ytdlp(url: str, height: int) -> Optional[str]:
    fmt = (f"bestvideo[protocol^=m3u8][vcodec^=avc1][height<={height}]"
           f"/best[protocol^=m3u8][height<={height}]")
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp", "--quiet", "--no-warnings", "--no-cache-dir", "--no-playlist",
        "--match-filter", "is_live", "--break-match-filters", "is_live", "-g", "-f", fmt, url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        proc.kill()
        log.warning("yt-dlp: Zeitüberschreitung für %s", url)
        return None
    lines = [x for x in out.decode().splitlines() if x.startswith("http")]
    if proc.returncode != 0 or not lines:
        log.warning("yt-dlp: keine HLS-Adresse für %s: %s", url, err.decode().strip()[:300])
        return None
    return lines[0]


async def resolve(url: str, height: int = MAIN_HEIGHT) -> Optional[str]:
    """go2rtc-Quelle (`ffmpeg:<hls>#video=copy`) für einen YouTube-Link, None wenn nicht verfügbar."""
    key = (url.strip(), height)
    now = time.time()
    hit = _cache.get(key)
    if hit and hit[1] > now:
        return hit[0]
    async with _lock:
        hit = _cache.get(key)
        if hit and hit[1] > time.time():
            return hit[0]
        hls = await _run_ytdlp(key[0], height)
        if not hls:
            _cache[key] = (None, time.time() + RETRY_S)
            return None
        m = _EXPIRE.search(hls)
        valid = min(int(m.group(1)) - REFRESH_BEFORE_S, time.time() + MAX_TTL_S) if m else time.time() + MAX_TTL_S
        src = f"ffmpeg:{hls}#input=youtube#video=copy"
        _cache[key] = (src, valid)
        log.info("YouTube-Stream aufgelöst: %s (%sp), gültig %d min", key[0], height, (valid - time.time()) // 60)
        return src
