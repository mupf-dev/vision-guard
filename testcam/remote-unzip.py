#!/usr/bin/env python3
"""Lädt einzelne Dateien aus einem entfernten ZIP per HTTP-Range (ohne das ganze Archiv zu laden).

    remote-unzip.py <url> <zielordner> <glob> [<glob> ...]

Hintergrund: Der Server der UTD-MHAD-Daten liefert nur ~40 KB/s je Verbindung; das ganze ZIP (1,1 GB) bräuchte
Stunden. Benötigt werden ~100 Clips; diese werden parallel über Range-Anfragen geholt. Nur Python-Standardbibliothek.
"""
from __future__ import annotations

import fnmatch
import os
import struct
import sys
import time
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor


def get(url: str, start: int, end: int, tries: int = 8) -> bytes:
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            if len(data) == end - start + 1:
                return data
        except Exception as e:  # noqa: BLE001
            print(f"  Wiederholung {i + 1} ({e})", file=sys.stderr)
        time.sleep(2 + i)
    raise RuntimeError(f"Range {start}-{end} nicht ladbar")


def main() -> None:
    url, out, pats = sys.argv[1], sys.argv[2], sys.argv[3:]
    os.makedirs(out, exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=60) as r:
        size = int(r.headers["Content-Length"])
    tail = get(url, max(0, size - 65536), size - 1)
    i = tail.rfind(b"PK\x05\x06")
    if i < 0:
        raise SystemExit("kein Zentralverzeichnis gefunden")
    _sig, _d, _cd, _n1, n, cd_size, cd_off, _cl = struct.unpack("<IHHHHIIH", tail[i:i + 22])
    cd = get(url, cd_off, cd_off + cd_size - 1)
    entries = []
    p = 0
    for _ in range(n):
        (_sig, _v, _vn, _fl, method, _t, _dt, _crc, csize, usize, nlen, xlen, clen, _dn, _ia, _ea,
         loff) = struct.unpack("<IHHHHHHIIIHHHHHII", cd[p:p + 46])
        name = cd[p + 46:p + 46 + nlen].decode("utf-8", "replace")
        p += 46 + nlen + xlen + clen
        if any(fnmatch.fnmatch(name, pat) for pat in pats):
            entries.append((name, method, csize, usize, loff))
    print(f"{len(entries)} von {n} Einträgen ausgewählt", flush=True)

    def fetch(e):
        name, method, csize, usize, loff = e
        dst = os.path.join(out, os.path.basename(name))
        if os.path.exists(dst) and os.path.getsize(dst) == usize:
            return name
        hdr = get(url, loff, loff + 29)
        nlen, xlen = struct.unpack("<HH", hdr[26:30])
        start = loff + 30 + nlen + xlen
        data = get(url, start, start + csize - 1)
        if method == 8:
            data = zlib.decompressobj(-15).decompress(data)
        elif method != 0:
            raise RuntimeError(f"{name}: Kompression {method} nicht unterstützt")
        with open(dst + ".part", "wb") as f:
            f.write(data)
        os.replace(dst + ".part", dst)
        return name

    with ThreadPoolExecutor(12) as ex:
        for k, name in enumerate(ex.map(fetch, entries), 1):
            print(f"  [{k}/{len(entries)}] {name}", flush=True)


if __name__ == "__main__":
    main()
