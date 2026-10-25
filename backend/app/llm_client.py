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

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

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
        return LLMError(f"{provider}: Zugriff verweigert (HTTP {resp.status_code}) – Schlüssel prüfen")
    return LLMError(f"{provider}: HTTP {resp.status_code}{(' – ' + detail) if detail else ''}")


def complete(messages: list[dict[str, str]], *, system: Optional[str] = None, max_tokens: int = 400,
             temperature: float = 0.2, timeout: Optional[float] = None) -> LLMResult:
    """Eine Chat-Antwort holen. `messages` = [{"role": "user"|"assistant", "content": str}]. Blockierend."""
    c = _cfg()
    provider, model = c["provider"], c["model"]
    if provider == "none":
        raise LLMError("Kein Sprachmodell eingerichtet (Einstellungen → KI-Sprachmodell)")
    if not model:
        raise LLMError("Kein Modell eingetragen (Einstellungen → KI-Sprachmodell)")
    to = float(timeout or c["timeout"])
    t0 = time.perf_counter()
    try:
        if provider == "openai_compat":
            if not c["base_url"]:
                raise LLMError("Keine Basis-URL eingetragen (Einstellungen → KI-Sprachmodell)")
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
                raise LLMError("VG_ANTHROPIC_API_KEY ist nicht gesetzt")
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
            raise LLMError(f"Unbekannter Anbieter: {provider}")
    except LLMError:
        raise
    except httpx.TimeoutException:
        raise LLMError(f"{provider}: keine Antwort binnen {to:.0f} s") from None
    except httpx.HTTPError as e:
        raise LLMError(f"{provider}: nicht erreichbar ({type(e).__name__})") from None
    except ValueError:
        raise LLMError(f"{provider}: ungültige Antwort (kein JSON)") from None
    ms = (time.perf_counter() - t0) * 1000
    if not text:
        raise LLMError(f"{provider}: leere Antwort")
    log.info("LLM %s/%s: %d Zeichen in %.0f ms", provider, used_model, len(text), ms)
    return LLMResult(text=text, model=used_model, provider=provider, ms=round(ms, 1), usage=dict(usage))
