import { useEffect } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useLive, useUi, toast } from "./store";
import type { Camera, WsMessage } from "./types";
import { labelName } from "./labels";
import { pushDetection } from "./overlaySync";

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

function handle(msg: WsMessage, qc: QueryClient) {
  const s = useLive.getState();
  switch (msg.type) {
    case "detections":
      pushDetection(msg);
      s.setDetections(msg);
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
      break;
    }
    case "inventory":
      s.applyInventory(msg);
      refreshInventory(qc);
      break;
    case "identity":
      qc.invalidateQueries({ queryKey: ["identities"] });
      qc.setQueryData(["identity", msg.identity.id], msg.identity);
      break;    case "notification": {
      qc.invalidateQueries({ queryKey: ["notifications"] });
      if (msg.action === "new" && "title" in msg.notification && useUi.getState().eventToasts) {
        const n = msg.notification;
        toast.event(n.title || "Hinweis", n.message, n.image_url);
      }
      break;
    }
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
