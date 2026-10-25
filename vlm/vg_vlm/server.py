"""HTTP-Schnittstelle des vlm-Dienstes (CONTRACT.md W3.E), nur intern (vlm:8200, kein Port nach außen).

    POST /infer {image_b64, question, region?, mode: "query"|"yesno", max_tokens?}
         → {answer, yes_no, ms, model}; 429 beschäftigt, 503 nicht verfügbar, 400 ungültiges Bild
    GET  /health → {state: "off|loading|ready|error: …", model, vram_mb, last_used, …}

Bilder und Fragen werden nicht gespeichert und nicht geloggt (das Backend legt das beantwortete Standbild ab).
"""
from __future__ import annotations

import base64
import binascii
import logging
import os
from contextlib import asynccontextmanager
from typing import Literal, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .engine import Busy, Engine, Unavailable

logging.basicConfig(level=os.environ.get("VG_LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("vg.vlm")
logging.getLogger("httpx").setLevel(logging.WARNING)     # Settings-Abruf alle 30 s nicht protokollieren

engine: Optional[Engine] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    engine = Engine()
    log.info("vlm-Dienst bereit (Modell wird erst bei der ersten Frage geladen)")
    try:
        yield
    finally:
        await run_in_threadpool(engine.shutdown)


app = FastAPI(title="VisionGuard vlm", lifespan=lifespan, docs_url=None, redoc_url=None)


class InferIn(BaseModel):
    image_b64: str = Field(min_length=16)
    question: str = Field(min_length=1, max_length=300)
    region: Optional[list[float]] = Field(None, min_length=4, max_length=4)
    mode: Literal["query", "yesno"] = "query"
    max_tokens: Optional[int] = Field(None, ge=1, le=512)


@app.post("/infer")
def infer(body: InferIn):
    try:
        img = base64.b64decode(body.image_b64, validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "Bild ungültig") from None
    try:
        return engine.infer(img, body.question, region=body.region, mode=body.mode, max_tokens=body.max_tokens)
    except Busy:
        raise HTTPException(429, "Bildmodell beschäftigt") from None
    except Unavailable as e:
        raise HTTPException(503, str(e)) from None
    except (OSError, ValueError, SyntaxError):          # PIL: kein lesbares Bild
        raise HTTPException(400, "Bild ungültig") from None


@app.get("/health")
def health():
    return engine.health() if engine else {"state": "off"}


@app.post("/unload")
def unload():
    """Modell sofort entladen und GPU zurückgeben (Wartung/Tests)."""
    engine.unload("manuell")
    return engine.health()


def main() -> None:
    # SIGTERM (docker stop): uvicorn beendet sauber → lifespan-Ende → Modell entladen, Lease zurückgeben
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("VG_VLM_PORT", "8200")), log_level="warning",
                access_log=False)


if __name__ == "__main__":
    main()
