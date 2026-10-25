import { useEffect } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useLive, useUi, toast } from "./store";
import type { Camera, WsMessage } from "./types";
import { labelName } from "./labels";
import { pushDetection } from "./overlaySync";
import { GESTURE_NAME, POSTURE_NAME, eventAlert } from "./pose";

// W2 C1: Notfall-Toast je Event und Wiederholung nur einmal
const alertToasted = new Set<string>();

function wsUrl(): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/api/ws`;
}

// Gegenstände: Listen/Suche gedrosselt neu laden (mehrere WS-Nachrichten pro Durchlauf möglich)
let invTimer: number | undefined;
function refreshInventory(qc: QueryClient) {
  if (invTimer !== undefined) return;
  invTimer = window.setTimeout(() => {
    invTimer = undefined;
    qc.invalidateQueries({ queryKey: ["inventory"] });
  }, 800);
}

let presTimer: number | undefined;
function refreshPresence(qc: QueryClient) {
  if (presTimer !== undefined) return;
  presTimer = window.setTimeout(() => {
    presTimer = undefined;
    qc.invalidateQueries({ queryKey: ["presence"] });
  }, 1000);
}

// Identitäten 07 (Paket A): Sammelaktionen senden eine gebündelte `identities`-Nachricht plus `identity` fürs Ziel –
// Listen (inkl. teurer Vorschläge) nur einmal, entprellt neu laden.
let identTimer: number | undefined;
let identBulk = false;
function refreshIdentities(qc: QueryClient, bulk: boolean) {
  identBulk ||= bulk;
  window.clearTimeout(identTimer);
  identTimer = window.setTimeout(() => {
    const all = identBulk;
    identTimer = undefined;
    identBulk = false;
    qc.invalidateQueries({ queryKey: ["identities"] });
    if (all) {
      for (const k of ["identity", "samples", "identity-events", "identity-suggestions", "identity-merges", "events"]) {
        qc.invalidateQueries({ queryKey: [k] });
      }
    }
  }, 400);
}

function handle(msg: WsMessage, qc: QueryClient) {
  const s = useLive.getState();
  switch (msg.type) {
    case "detections":
      pushDetection(msg);
      s.setDetections(msg);
      break;
    case "ptz":              // PTZ: Zustand je Kamera (Steuerkreuz, Zonen nur in der Grundposition)
      s.setPtz(msg.camera_id, msg.state);
      qc.setQueryData<Camera[]>(["cameras"], (old) =>
        old?.map((c) => (c.id === msg.camera_id ? { ...c, ptz_state: msg.state } : c)),
      );
      break;
    case "camera_status":
      s.setStatus(msg.camera_id, msg.status);
      qc.setQueryData<Camera[]>(["cameras"], (old) =>
        old?.map((c) => (c.id === msg.camera_id ? { ...c, status: msg.status } : c)),
      );
      break;
    case "event": {
      s.upsertEvent(msg.event);
      qc.invalidateQueries({ queryKey: ["events"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      if (msg.event.identity) qc.invalidateQueries({ queryKey: ["identity-events", msg.event.identity.id] });
      if (msg.action === "new" && useUi.getState().eventToasts && msg.event.rules?.length) {
        const e = msg.event;
        toast.event(
          `${labelName(e.label)} – ${e.camera_name}`,
          `${e.identity?.display_name ?? ""}${e.identity ? " · " : ""}Regel: ${e.rules.join(", ")}`,
          e.thumb_url,
        );
      }
      const al = eventAlert(msg.event);
      if (al && msg.event.active && !msg.event.acknowledged_at && useUi.getState().eventToasts) {
        const k = `${msg.event.id}:${al.repeat}`;
        if (!alertToasted.has(k)) {
          alertToasted.add(k);
          const what = al.posture ? `Person ${POSTURE_NAME[al.posture]}` : al.rule;
          toast.event(`Notfall: ${what}`, `Kamera ${msg.event.camera_name} · Regel „${al.rule}“${al.repeat ? ` · Wiederholung ${al.repeat}` : ""}`,
            msg.event.snapshot_url ?? msg.event.thumb_url);
        }
      }
      break;
    }
    case "inventory":
      s.applyInventory(msg);
      refreshInventory(qc);
      break;
    case "identity":
      qc.setQueryData(["identity", msg.identity.id], msg.identity);
      refreshIdentities(qc, false);
      break;
    case "identities":       // Identitäten 07: gebündelt (merged|deleted)
      refreshIdentities(qc, true);
      break;
    case "nightly":          // Identitäten 07 (Paket D): Nachtlauf gestartet/fertig
      qc.invalidateQueries({ queryKey: ["nightly"] });
      break;
    case "notification": {
      qc.invalidateQueries({ queryKey: ["notifications"] });
      if (msg.action === "new" && "title" in msg.notification && useUi.getState().eventToasts) {
        const n = msg.notification;
        toast.event(n.title || "Hinweis", n.message, n.image_url);
      }
      break;
    }
    // W2 (K2): Grundverhalten – Fach-Agenten ergänzen Toasts/Overlay (D2, C1, C3, B3)
    case "review":
      qc.invalidateQueries({ queryKey: ["reviews"] });
      break;
    case "gesture": {
      // D1: kurz im Live-Overlay am Track einblenden + Toast (wenn Alarm-Benachrichtigungen an)
      s.setGesture(msg);
      if (useUi.getState().eventToasts) {
        const who = msg.identity?.display_name ?? "Person";
        toast.event(`Geste: ${GESTURE_NAME[msg.gesture] ?? msg.gesture}`, `${who} · Kamera ${msg.camera_name ?? msg.camera_id} · ${msg.held_s.toFixed(1)} s gehalten`);
      }
      break;
    }
    case "pet_visit":
      qc.invalidateQueries({ queryKey: ["pets"] });
      break;
    case "sensor":           // W3 (A3)
      qc.invalidateQueries({ queryKey: ["sensors"] });
      break;
    case "shopping_list":    // W3 (C4)
      qc.invalidateQueries({ queryKey: ["shopping"] });
      break;
    case "report":
      qc.invalidateQueries({ queryKey: ["reports"] });
      break;
    case "presence":         // W4 (C2): Anwesenheit gedrosselt neu laden
      refreshPresence(qc);
      break;
  }
}

/** Einziger WebSocket der App. Verteilt Nachrichten über den zustand-Store. */
export function useLiveSocket(enabled: boolean) {
  const qc = useQueryClient();
  useEffect(() => {
    if (!enabled) return;
    let ws: WebSocket | null = null;
    let stopped = false;
    let attempt = 0;
    let timer: number | undefined;
    const setWs = useLive.getState().setWs;

    const connect = () => {
      if (stopped) return;
      setWs("connecting");
      ws = new WebSocket(wsUrl());
      ws.onopen = () => {
        attempt = 0;
        setWs("open");
        // nach Reconnect evtl. verpasste Daten nachladen
        qc.invalidateQueries({ queryKey: ["events"] });
        qc.invalidateQueries({ queryKey: ["cameras"] });
        qc.invalidateQueries({ queryKey: ["inventory"] });
      };
      ws.onmessage = (ev) => {
        try {
          handle(JSON.parse(ev.data) as WsMessage, qc);
        } catch (e) {
          console.warn("WS-Nachricht nicht verarbeitet", e);
        }
      };
      ws.onclose = () => {
        setWs("closed");
        ws = null;
        if (stopped) return;
        const delay = Math.min(30000, 1000 * 2 ** attempt) + Math.random() * 500;
        attempt++;
        timer = window.setTimeout(connect, delay);
      };
      ws.onerror = () => ws?.close();
    };
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      ws?.close();
    };
  }, [enabled, qc]);
}
