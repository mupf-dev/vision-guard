"""Cloud-Fallback für „Frag die Kamera“ (A2) – Gerüst aus K3, Umsetzung: A2.

Vertrag: CONTRACT.md W3.E. Nur wenn `vlm_cloud_model` gesetzt ist, `llm_provider` ≠ none und die Kamera
`vlm_cloud_allowed=true` hat. Aufruf über den gemeinsamen Client:

    from . import llm_client
    res = llm_client.complete([{"role": "user", "content": question}], images=[jpeg_bytes],
                              model=settings["vlm_cloud_model"], max_tokens=200)

Schlüssel nur aus der Env (`VG_ANTHROPIC_API_KEY` bzw. `VG_LLM_API_KEY`), nie loggen. Getestet wird
`openai_compat` gegen `scripts/llm_mock.py` (ohne Kosten).
"""
from __future__ import annotations

from typing import Any


def available(camera: dict[str, Any] | None = None) -> bool:
    """True, wenn der Cloud-Fallback (für diese Kamera) erlaubt und eingerichtet ist (A2)."""
    return False
