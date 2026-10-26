"""Gemeinsamer LLM-Client (W2, K2) – genutzt von B3 (Tagesbericht), später A2 (Cloud-Fallback).

Standardmäßig AUS (`llm_provider: "none"`). Anbieter:
- `openai_compat`: `POST {llm_base_url}/chat/completions` (Ollama, llama.cpp, vLLM, LLM-Bridge, Test-Mock
  `scripts/llm_mock.py`); Schlüssel optional aus Env `VG_LLM_API_KEY` (Bearer).
- `anthropic`: Messages-API, Schlüssel aus Env `VG_ANTHROPIC_API_KEY` (Header `x-api-key`).
Schlüssel stehen nie in Settings, API-Antworten, Fehlermeldungen oder Logs. Kein zusätzliches Paket (httpx).

    from . import llm_client
    if llm_client.configured():
        res = llm_client.complete([{"role": "user", "content": "…"}], system="Antworte auf Deutsch.", max_tokens=300)
        res.text
"""
from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from . import i18n
from . import settings_store
from .config import config

log = logging.getLogger("vg.llm")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class LLMError(Exception):
    """Deutsche Meldung ohne Schlüssel/Geheimnisse."""


@dataclass
class LLMResult:
    text: str
    model: str
    provider: str
    ms: float
    usage: dict[str, Any] = field(default_factory=dict)


def _cfg() -> dict[str, Any]:
    st = settings_store.get()
    return {"provider": st.get("llm_provider") or "none", "base_url": (st.get("llm_base_url") or "").rstrip("/"),
            "model": st.get("llm_model") or "", "timeout": float(st.get("llm_timeout_s") or 60)}


def configured() -> bool:
    """True, wenn ein Anbieter mit Modell (und bei anthropic ein Schlüssel) eingerichtet ist."""
    c = _cfg()
    if c["provider"] == "openai_compat":
        return bool(c["base_url"] and c["model"])
    if c["provider"] == "anthropic":
        return bool(c["model"] and config.anthropic_api_key)
    return False


def _status_error(provider: str, resp: httpx.Response) -> LLMError:
    detail = ""
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            detail = str(err.get("message") or err.get("type") or "")[:200]
        elif isinstance(err, str):
            detail = err[:200]
    except Exception:  # noqa: BLE001
        pass
    if resp.status_code in (401, 403):
        return LLMError(i18n.t("errors.llm_access_denied", provider=provider, status=resp.status_code))
    return LLMError(f"{provider}: HTTP {resp.status_code}{(' – ' + detail) if detail else ''}")


def _image_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _with_images(provider: str, messages: list[dict[str, Any]], images: list[bytes]) -> list[dict[str, Any]]:
    """W3 (A2): Bilder an die letzte user-Nachricht hängen (openai_compat: image_url/data-URL, anthropic: image)."""
    msgs = [dict(m) for m in messages]
    idx = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=None)
    if idx is None:
        msgs.append({"role": "user", "content": ""})
        idx = len(msgs) - 1
    text = msgs[idx].get("content")
    parts: list[dict[str, Any]] = []
    for img in images:
        mime, b64 = _image_mime(img), base64.b64encode(img).decode()
        if provider == "anthropic":
            parts.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}})
        else:
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    if isinstance(text, str) and text:
        parts.append({"type": "text", "text": text})
    elif isinstance(text, list):
        parts.extend(text)
    msgs[idx]["content"] = parts
    return msgs


def complete(messages: list[dict[str, Any]], *, system: Optional[str] = None, max_tokens: int = 400,
             temperature: float = 0.2, timeout: Optional[float] = None, images: Optional[list[bytes]] = None,
             model: Optional[str] = None) -> LLMResult:
    """Eine Chat-Antwort holen. `messages` = [{"role": "user"|"assistant", "content": str}]. Blockierend.

    W3: `images` (JPEG/PNG-Bytes) hängen an der letzten user-Nachricht (Bildmodelle, A2-Cloud-Fallback);
    `model` überschreibt das Setting `llm_model` (z. B. `vlm_cloud_model`)."""
    c = _cfg()
    provider, model = c["provider"], (model or c["model"])
    if images:
        messages = _with_images(provider, messages, images)
    if provider == "none":
        raise LLMError(i18n.t("errors.llm_not_configured"))
    if not model:
        raise LLMError(i18n.t("errors.llm_no_model"))
    to = float(timeout or c["timeout"])
    t0 = time.perf_counter()
    try:
        if provider == "openai_compat":
            if not c["base_url"]:
                raise LLMError(i18n.t("errors.llm_no_base_url"))
            msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
            headers = {"Content-Type": "application/json"}
            if config.llm_api_key:
                headers["Authorization"] = f"Bearer {config.llm_api_key}"
            resp = httpx.post(f"{c['base_url']}/chat/completions", headers=headers, timeout=to,
                              json={"model": model, "messages": msgs, "max_tokens": int(max_tokens),
                                    "temperature": float(temperature), "stream": False})
            if resp.status_code >= 400:
                raise _status_error("openai_compat", resp)
            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            text = ((choice.get("message") or {}).get("content") or choice.get("text") or "").strip()
            usage = data.get("usage") or {}
            used_model = str(data.get("model") or model)
        elif provider == "anthropic":
            if not config.anthropic_api_key:
                raise LLMError(i18n.t("errors.llm_anthropic_key_missing"))
            body: dict[str, Any] = {"model": model, "max_tokens": int(max_tokens), "temperature": float(temperature),
                                    "messages": list(messages)}
            if system:
                body["system"] = system
            resp = httpx.post(ANTHROPIC_URL, timeout=to, json=body,
                              headers={"x-api-key": config.anthropic_api_key, "anthropic-version": ANTHROPIC_VERSION,
                                       "content-type": "application/json"})
            if resp.status_code >= 400:
                raise _status_error("anthropic", resp)
            data = resp.json()
            text = "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text").strip()
            usage = data.get("usage") or {}
            used_model = str(data.get("model") or model)
        else:
            raise LLMError(i18n.t("errors.llm_unknown_provider", provider=provider))
    except LLMError:
        raise
    except httpx.TimeoutException:
        raise LLMError(i18n.t("errors.llm_timeout", provider=provider, seconds=f"{to:.0f}")) from None
    except httpx.HTTPError as e:
        raise LLMError(i18n.t("errors.llm_unreachable", provider=provider, error=type(e).__name__)) from None
    except ValueError:
        raise LLMError(i18n.t("errors.llm_invalid_response", provider=provider)) from None
    ms = (time.perf_counter() - t0) * 1000
    if not text:
        raise LLMError(i18n.t("errors.llm_empty_response", provider=provider))
    log.info("LLM %s/%s: %d Zeichen in %.0f ms", provider, used_model, len(text), ms)
    return LLMResult(text=text, model=used_model, provider=provider, ms=round(ms, 1), usage=dict(usage))
