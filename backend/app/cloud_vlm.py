"""Cloud-Fallback für „Frag die Kamera“ (W3, A2). Vertrag: CONTRACT.md W3.E.

Nur wenn `vlm_cloud_model` gesetzt ist, `llm_provider` ≠ none (openai_compat mit Basis-URL bzw. anthropic mit
Schlüssel in der Env) **und** die Kamera `vlm_cloud_allowed=true` hat. Standard: aus (`vlm_cloud_model: null`).
Aufruf über den gemeinsamen Client `llm_client.complete(..., images=[jpeg], model=vlm_cloud_model)`.
Schlüssel nur aus der Env (`VG_ANTHROPIC_API_KEY` bzw. `VG_LLM_API_KEY`), nie geloggt. Getestet wird
`openai_compat` gegen `scripts/llm_mock.py` (ohne Kosten).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from . import llm_client, settings_store
from .config import config

log = logging.getLogger("vg.cloud_vlm")

CLOUD_MAX_W = 1600          # Bild vor dem Versand verkleinern (Kosten/Bandbreite, reicht für Fragen)
SYSTEM = ("You look at a single still image from a private home camera. Answer the user's question briefly and "
          "only from what is visible. If it cannot be seen, say so. Answer in the language of the question.")


def configured() -> bool:
    """Cloud-Modell und Anbieter eingerichtet (unabhängig von der Kamera)."""
    st = settings_store.get()
    if not (st.get("vlm_cloud_model") or "").strip():
        return False
    provider = st.get("llm_provider") or "none"
    if provider == "openai_compat":
        return bool((st.get("llm_base_url") or "").strip())
    if provider == "anthropic":
        return bool(config.anthropic_api_key)
    return False


def available(camera: dict[str, Any] | None = None) -> bool:
    """True, wenn der Cloud-Fallback (für diese Kamera) erlaubt und eingerichtet ist."""
    if camera is not None and not camera.get("vlm_cloud_allowed"):
        return False
    return configured()


def ask(image: bytes, question: str, region: Optional[list[float]], mode: str) -> dict[str, Any]:
    """Frage an das Cloud-Modell → {answer, yes_no, model, ms}. Wirft vlm.AskError(503)."""
    from .vlm import AskError, crop_jpeg, parse_yes_no

    st = settings_store.get()
    model = (st.get("vlm_cloud_model") or "").strip()
    q = question.strip()
    if mode == "yesno":
        q += " Answer with yes or no first (in English: 'Yes'/'No', in German: 'Ja'/'Nein'), then explain in one short sentence."
    img = crop_jpeg(image, region, CLOUD_MAX_W)
    try:
        res = llm_client.complete([{"role": "user", "content": q}], system=SYSTEM, images=[img], model=model,
                                  max_tokens=200, temperature=0.0, timeout=40)
    except llm_client.LLMError as e:
        log.warning("Cloud-Bildmodell: %s", e)
        raise AskError(503, f"Bildmodell nicht verfügbar (Cloud: {e})") from None
    except Exception as e:  # noqa: BLE001
        log.warning("Cloud-Bildmodell: %s", type(e).__name__)
        raise AskError(503, "Bildmodell nicht verfügbar (Cloud)") from None
    answer = (res.text or "").strip()
    return {"answer": answer, "yes_no": parse_yes_no(answer), "model": res.model or model, "ms": res.ms}
