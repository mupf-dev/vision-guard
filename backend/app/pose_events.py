"""Pose, Sturz/Liegen und Gesten im Backend (C1/D1) – Gerüst aus K2, Umsetzung: C1+D1.

Vertrag: CONTRACT.md „Nachträge: Welle 2“, W2.C/W2.I. Verdrahtung ist fertig:
- `rules.py` registriert die Trigger `posture` (PostureTriggerOptions) und `gesture` (GestureTriggerOptions) ohne
  Evaluator. C1 reicht die Evaluatoren hier nach:
      rules.register_trigger("posture", track_based=True, options_model=PostureTriggerOptions,
                             label_de="Haltung (z. B. liegt am Boden)", evaluate=_eval_posture)
  `evaluate(TriggerContext) -> list[str] | None` wird von `events.Processor` in den Phasen start/update/end, jede
  Sekunde mit `tick` und für Gesten mit `gesture` aufgerufen (`ctx.state` bleibt je Regel+Track erhalten).
- `mqtt_bus` leitet `vg/internal/gesture/{camera_id}` (Track-Thread) an `handle_gesture()` weiter.
  Für die Regelauswertung: `events.processor.dispatch_track_phase(camera_id, track_id, "gesture", msg)`.
- `POST /events/{id}/ack` setzt `acknowledged_at` (fertig, routers/events.py).

C1 ergänzt: Haltungs-/Gesten-Evaluatoren, Wiederholung `repeat_s/repeat_max` und `{posture}`/`{gesture}`/`{repeat}`
in `events.py`, WS `gesture`, MQTT extern `{prefix}/gesture/{camera_slug}`, Nachlauf `fall_track_grace_s`.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("vg.pose")


def handle_gesture(camera_id: Optional[int], msg: dict[str, Any]) -> None:
    """Worker-Nachricht `vg/internal/gesture/{camera_id}` (C1/D1)."""
    return None
