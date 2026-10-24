import { create } from "zustand";
import type { CameraStatus, DetectionsMsg, VGEvent } from "./types";

export type WsState = "connecting" | "open" | "closed";

interface LiveState {
  ws: WsState;
  detections: Record<number, DetectionsMsg & { receivedAt: number }>;
  status: Record<number, CameraStatus>;
  feed: VGEvent[];
  setWs: (s: WsState) => void;
  setDetections: (m: DetectionsMsg) => void;
  setStatus: (id: number, s: CameraStatus) => void;
  upsertEvent: (e: VGEvent) => void;
  seedFeed: (events: VGEvent[]) => void;
}

const FEED_MAX = 80;

export const useLive = create<LiveState>((set) => ({
  ws: "closed",
  detections: {},
  status: {},
  feed: [],
  setWs: (ws) => set({ ws }),
  setDetections: (m) =>
    set((s) => ({ detections: { ...s.detections, [m.camera_id]: { ...m, receivedAt: Date.now() } } })),
  setStatus: (id, st) => set((s) => ({ status: { ...s.status, [id]: st } })),
  upsertEvent: (e) =>
    set((s) => {
      const idx = s.feed.findIndex((x) => x.id === e.id);
      if (idx >= 0) {
        const feed = s.feed.slice();
        feed[idx] = e;
        return { feed };
      }
      return { feed: [e, ...s.feed].slice(0, FEED_MAX) };
    }),
  seedFeed: (events) =>
    set((s) => {
      const known = new Set(s.feed.map((e) => e.id));
      const merged = [...s.feed, ...events.filter((e) => !known.has(e.id))];
      merged.sort((a, b) => (a.start_ts < b.start_ts ? 1 : -1));
      return { feed: merged.slice(0, FEED_MAX) };
    }),
}));

/* ---------- Toasts ---------- */
export type ToastKind = "info" | "success" | "error" | "event";
export interface Toast {
  id: number;
  kind: ToastKind;
  title: string;
  text?: string;
  image?: string | null;
}

interface ToastState {
  toasts: Toast[];
  push: (t: Omit<Toast, "id">, ttl?: number) => void;
  dismiss: (id: number) => void;
}

let toastSeq = 1;
export const useToasts = create<ToastState>((set, get) => ({
  toasts: [],
  push: (t, ttl = 4500) => {
    const id = toastSeq++;
    set((s) => ({ toasts: [...s.toasts, { ...t, id }].slice(-5) }));
    window.setTimeout(() => get().dismiss(id), ttl);
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })),
}));

export const toast = {
  info: (title: string, text?: string) => useToasts.getState().push({ kind: "info", title, text }),
  success: (title: string, text?: string) => useToasts.getState().push({ kind: "success", title, text }),
  error: (title: string, text?: string) => useToasts.getState().push({ kind: "error", title, text }, 7000),
  event: (title: string, text?: string, image?: string | null) =>
    useToasts.getState().push({ kind: "event", title, text, image }),
};

/* ---------- UI-Präferenzen (persistiert) ---------- */
interface UiState {
  layout: "auto" | 1 | 2 | 3 | 4;
  showZones: boolean;
  feedOpen: boolean;
  eventToasts: boolean;
  /** manueller Overlay-Versatz je Kamera in Sekunden (zusätzlich zur automatisch gemessenen Video-Verzögerung) */
  overlayDelay: Record<number, number>;
  set: (p: Partial<Omit<UiState, "set">>) => void;
}

const UI_KEY = "vg.ui";
const initialUi = (() => {
  try {
    return JSON.parse(localStorage.getItem(UI_KEY) ?? "{}");
  } catch {
    return {};
  }
})();

export const useUi = create<UiState>((set) => ({
  layout: "auto",
  showZones: false,
  feedOpen: true,
  eventToasts: true,
  overlayDelay: {},
  ...initialUi,
  set: (p) =>
    set((s) => {
      const next = { ...s, ...p };
      const { set: _ignored, ...persist } = next;
      void _ignored;
      localStorage.setItem(UI_KEY, JSON.stringify(persist));
      return next;
    }),
}));
