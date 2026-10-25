"""Pose, Sturz/Liegen und Gesten im Backend (C1/D1).

Vertrag: CONTRACT.md „Nachträge: Welle 2“, W2.C/W2.I.
- Trigger `posture`: Evaluator `_eval_posture` – die Haltung kommt vom Worker (`attributes.posture`,
  `posture_since`, `motion`, über 2 s geglättet). Ausgelöst wird einmal je Track und Regel, sobald die Haltung
  `min_dwell_s` besteht, die Person in keiner `exclude_zone_ids`-Zone (Sofa, Bett) und – falls gesetzt – in einer
  der `zone_ids` ist und die Bewegung `max_motion` nicht übersteigt. Wechselt die Haltung für ≥ `REARM_S`, kann
  die Regel für denselben Track erneut auslösen (Cooldown gilt wie immer).
- Trigger `gesture`: Evaluator `_eval_gesture` – nur in der Phase `gesture` (aus `vg/internal/gesture/{cid}`).
- Wiederholung `repeat_s/repeat_max` und Quittieren setzt `events.Processor` um (`_do_fire`, `_tick_repeats`);
  `ctx.state["holding"]` sagt dort, ob die Haltung noch besteht (sonst keine Wiederholung).
- `handle_gesture`: WS `gesture`, MQTT extern `{prefix}/gesture/{camera_slug}`, Regelauswertung.
- Nachlauf `fall_track_grace_s` (Track-Abriss im Liegen): im Worker (`tracking.py`), der Track bleibt bestehen.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from . import registry, rules, settings_store
from .schemas import GestureTriggerOptions, PostureTriggerOptions
from .ws import hub

log = logging.getLogger("vg.pose")

REARM_S = 10.0     # so lange muss die Haltung weg sein, bis dieselbe Regel für denselben Track erneut auslöst

POSTURE_DE = {"lying": "liegt", "sitting": "sitzt", "standing": "steht", "unknown": "unbekannt"}
GESTURE_DE = {"arms_up": "beide Arme hoch", "arm_left": "linker Arm seitlich",
              "arm_right": "rechter Arm seitlich", "arms_cross": "Arme über dem Kopf gekreuzt"}


def posture_de(p: Optional[str]) -> str:
    return POSTURE_DE.get(p or "", "") if p else ""


def gesture_de(g: Optional[str]) -> str:
    return GESTURE_DE.get(g or "", g or "")


# ------------------------------------------------------------------ Evaluatoren


def _eval_posture(ctx: rules.TriggerContext) -> Optional[list[str]]:
    st, tr, rule, now = ctx.state, ctx.track, ctx.rule, ctx.now
    if ctx.phase not in ("start", "update", "tick"):
        st["holding"] = False
        return None
    opts = rule.get("trigger_options") or {}
    want = opts.get("posture") or "lying"
    attrs = tr.attributes or {}
    cur = attrs.get("posture")
    if cur != want:
        if st.get("holding"):
            st["off_since"] = now
        st["holding"] = False
        if st.get("fired") and now - float(st.get("off_since") or now) >= REARM_S:
            st["fired"] = False          # Haltung lange genug weg → neu scharf
        return None
    st.pop("off_since", None)
    zones = [str(z) for z in tr.zones or []]
    excl = {str(z) for z in opts.get("exclude_zone_ids") or []}
    rz = [str(z) for z in rule.get("zone_ids") or []]
    hit = [z for z in zones if z in rz] if rz else list(zones)
    bad = bool(excl & set(zones)) or (bool(rz) and not hit)
    mm = opts.get("max_motion")
    motion = attrs.get("motion")
    if not bad and mm is not None and motion is not None and float(motion) > float(mm):
        bad = True
    if bad:
        # Dauer zählt erst ab dem Zeitpunkt, ab dem alle Bedingungen wieder erfüllt sind
        st["last_bad"] = now
        st["holding"] = False
        return None
    try:
        since = float(attrs.get("posture_since") or now)
    except (TypeError, ValueError):
        since = now
    since = min(max(since, float(st.get("last_bad") or 0.0), tr.started_rx - 30.0), now)
    st["holding"] = True
    st["since"] = since
    if st.get("fired"):
        return None
    if now - since >= float(rule.get("min_dwell_s") or 0):
        st["fired"] = True
        st["placeholders"] = {"posture": posture_de(want)}
        return hit
    return None


def _eval_gesture(ctx: rules.TriggerContext) -> Optional[list[str]]:
    if ctx.phase != "gesture":
        return None
    g = ctx.msg.get("gesture")
    wanted = (ctx.rule.get("trigger_options") or {}).get("gestures") or []
    if g not in wanted:
        return None
    zones = [str(z) for z in (ctx.msg.get("zones") if ctx.msg.get("zones") is not None else ctx.track.zones) or []]
    rz = [str(z) for z in ctx.rule.get("zone_ids") or []]
    if rz:
        hit = [z for z in zones if z in rz]
        if not hit:
            return None
        zones = hit
    ctx.state["placeholders"] = {"gesture": gesture_de(g)}
    return zones


rules.register_trigger("posture", track_based=True, options_model=PostureTriggerOptions,
                       label_de="Haltung (z. B. liegt am Boden)", evaluate=_eval_posture)
rules.register_trigger("gesture", track_based=True, options_model=GestureTriggerOptions, label_de="Geste",
                       evaluate=_eval_gesture)


# ------------------------------------------------------------------ Gesten-Nachrichten


def handle_gesture(camera_id: Optional[int], msg: dict[str, Any]) -> None:
    """Worker-Nachricht `vg/internal/gesture/{camera_id}` (läuft im Track-Thread)."""
    from .events import processor

    if camera_id is None:
        return
    gesture = msg.get("gesture")
    if gesture not in GESTURE_DE:
        log.warning("Unbekannte Geste von Kamera %s: %r", camera_id, gesture)
        return
    cam = registry.get(camera_id)
    if cam is None:
        return
    data = {k: msg.get(k) for k in ("camera_id", "track_id", "gesture", "score", "held_s", "ts", "zones", "identity")}
    data["camera_id"] = camera_id
    data["camera_name"] = cam["name"]
    log.info("Geste %s an Kamera %s (Track %s, %.1f s)", gesture, cam["name"], msg.get("track_id"),
             float(msg.get("held_s") or 0))
    hub.broadcast({"type": "gesture", **data})
    prefix = settings_store.get()["mqtt_prefix"]
    processor._pub(f"{prefix}/gesture/{cam['slug']}", {
        "gesture": gesture, "camera": cam["name"], "camera_id": camera_id, "identity": msg.get("identity"),
        "ts": msg.get("ts")}, qos=1)
    try:
        tid = int(msg.get("track_id"))
    except (TypeError, ValueError):
        return
    if not processor.dispatch_track_phase(camera_id, tid, "gesture", msg):
        log.info("Geste für unbekannten Track %s/%s – keine Regelauswertung", camera_id, tid)
