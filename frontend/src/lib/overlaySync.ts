/**
 * Synchronisation der Detektions-Overlays mit dem Live-Video.
 *
 * Problem: Detektionen kommen per WebSocket praktisch sofort, das Video im Browser (WebRTC/MSE über go2rtc) hat eine
 * eigene, schwankende Latenz; bei YouTube/HLS liegen Analyse- und Anzeigestream sogar in getrennten Pipelines.
 * Lösung:
 *  1. Jede Detektion wird pro Kamera gepuffert und mit ihrem Frame-Zeitstempel `ts` (Worker-Wanduhr beim Einlesen)
 *     auf die Browser-Uhr umgerechnet (Uhrversatz = Minimum von Empfangszeit − ts über 30 s).
 *  2. Ein VideoMonitor misst am <video> die Darstellungsverzögerung (WebRTC: expectedDisplayTime − receiveTime aus
 *     requestVideoFrameCallback, MSE: Pufferlänge buffered.end − currentTime) und erkennt ein hängendes Bild
 *     (keine neuen Frames).
 *  3. Gezeichnet wird die Detektion zum Zeitpunkt (jetzt − Verzögerung − manueller Versatz), zwischen zwei
 *     Analyse-Frames linear interpoliert. Hängt das Video, bleibt die Overlay-Zeit beim letzten Bild stehen.
 */
import { useEffect, useState, type RefObject } from "react";
import type { DetObject, DetectionsMsg } from "./types";
import { useUi } from "./store";

/** Ohne Detektion in dieser Zeit (relativ zur Overlay-Zeit) verschwinden die Boxen. */
export const STALE_MS = 1500;
/** Ab dieser Standzeit gilt das Video als hängend, mindestens jedoch 4 Frame-Intervalle. */
const STALL_MIN_MS = 1000;
/** Ab dieser Standzeit werden die eingefrorenen Boxen abgeblendet und "Video hängt" angezeigt. */
export const STALL_SHOW_MS = 1500;
/** Maximaler Abstand zweier Analyse-Frames, zwischen denen noch interpoliert wird. */
const INTERP_MAX_MS = 700;
const BUFFER_MS = 15000;
const SKEW_WINDOW_S = 30;
export const OFFSET_MIN_S = -1;
export const OFFSET_MAX_S = 5;

/* ---------------- Detektionspuffer ---------------- */

interface BufferedDet { t: number; ts: number; objects: DetObject[] }   // t = Frame-Zeit in Browser-Uhr (ms)

const buffers = new Map<number, BufferedDet[]>();
// Minimum (Empfangszeit − ts) je Sekunde -> Uhrversatz Worker/Browser inkl. minimaler Transportlatenz
const skewBuckets = new Map<number, number>();
let skew: number | null = null;

function updateSkew(now: number, sample: number) {
  const sec = Math.floor(now / 1000);
  const cur = skewBuckets.get(sec);
  if (cur === undefined || sample < cur) skewBuckets.set(sec, sample);
  let min = Infinity;
  for (const [s, v] of skewBuckets) {
    if (s < sec - SKEW_WINDOW_S) skewBuckets.delete(s);
    else if (v < min) min = v;
  }
  const prev = skew;
  skew = min;
  // Uhrversatz hat sich geändert (z. B. Uhrsprung): bereits gepufferte Zeitstempel nachziehen
  if (prev !== null && prev !== min) {
    const d = min - prev;
    for (const list of buffers.values()) for (const b of list) b.t += d;
  }
}

/** Neue Detektionsnachricht aufnehmen (aus useLiveSocket). */
export function pushDetection(m: DetectionsMsg) {
  const now = Date.now();
  const tsMs = typeof m.ts === "number" && m.ts > 0 ? m.ts * 1000 : now;
  updateSkew(now, now - tsMs);
  const t = tsMs + (skew ?? 0);
  let list = buffers.get(m.camera_id);
  if (!list) buffers.set(m.camera_id, (list = []));
  // nach Zeit sortiert halten (Nachrichten kommen praktisch immer in Reihenfolge)
  let i = list.length;
  while (i > 0 && list[i - 1].t > t) i--;
  if (i > 0 && list[i - 1].ts === m.ts) list[i - 1] = { t, ts: m.ts, objects: m.objects };
  else list.splice(i, 0, { t, ts: m.ts, objects: m.objects });
  let drop = 0;
  while (drop < list.length && list[drop].t < now - BUFFER_MS) drop++;
  if (drop) list.splice(0, drop);
}

export function dropCameraBuffer(cameraId: number) {
  buffers.delete(cameraId);
}

/* ---------------- Video-Monitor ---------------- */

export type VideoMode = "none" | "webrtc" | "mse" | "mjpeg";

export interface VideoClock {
  mode: VideoMode;
  /** gemessene Darstellungsverzögerung des Players (ms) */
  delayMs: number;
  /** Wanduhrzeit (ms) des zuletzt dargestellten Frames, 0 = noch keiner */
  lastFrameAt: number;
  stalled: boolean;
}

type FrameMeta = { expectedDisplayTime?: number; receiveTime?: number };
type RvfcVideo = HTMLVideoElement & {
  requestVideoFrameCallback?: (cb: (now: number, meta: FrameMeta) => void) => number;
  cancelVideoFrameCallback?: (h: number) => void;
};

const clocks = new Map<number, VideoClock>();

// Diagnose in der Browser-Konsole: window.__vgSync.clocks / .buffers / .skew()
(window as unknown as Record<string, unknown>).__vgSync = { clocks, buffers, skew: () => skew };

export function getVideoClock(cameraId: number): VideoClock | undefined {
  return clocks.get(cameraId);
}

class VideoMonitor {
  private video: RvfcVideo | null = null;
  private rvfc = 0;
  private timer: number;
  private lastFrameAt = 0;
  private frameIv = 0;      // EMA Frame-Intervall (ms)
  private rtcLat: number | null = null;
  private mseLat: number | null = null;
  private lastCount = -1;

  constructor(private cameraId: number, private find: () => HTMLVideoElement | null) {
    this.timer = window.setInterval(() => this.poll(), 200);
    this.poll();
  }

  stop() {
    window.clearInterval(this.timer);
    this.detach();
    clocks.delete(this.cameraId);
  }

  private detach() {
    if (this.video && this.rvfc) this.video.cancelVideoFrameCallback?.(this.rvfc);
    this.rvfc = 0;
    this.video = null;
  }

  private attach(v: RvfcVideo | null) {
    this.detach();
    this.video = v;
    this.lastFrameAt = 0;
    this.frameIv = 0;
    this.rtcLat = this.mseLat = null;
    this.lastCount = -1;
    if (v?.requestVideoFrameCallback) this.rvfc = v.requestVideoFrameCallback(this.onFrame);
  }

  private frame(wall: number, interval = true) {
    if (interval && this.lastFrameAt) {
      const iv = wall - this.lastFrameAt;
      if (iv > 0 && iv < 2000) this.frameIv = this.frameIv ? this.frameIv * 0.9 + iv * 0.1 : iv;
    }
    this.lastFrameAt = wall;
  }

  private onFrame = (_now: number, meta: FrameMeta) => {
    const v = this.video;
    if (!v) return;
    this.frame(Date.now());
    // WebRTC: Zeit vom Empfang des letzten Pakets bis zur Anzeige (Jitterpuffer + Dekodieren)
    if (meta.receiveTime && meta.expectedDisplayTime) {
      const lat = meta.expectedDisplayTime - meta.receiveTime;
      if (lat >= 0 && lat < 5000) this.rtcLat = this.rtcLat === null ? lat : this.rtcLat * 0.95 + lat * 0.05;
    }
    this.rvfc = v.requestVideoFrameCallback!(this.onFrame);
  };

  private poll() {
    const found = this.find() as RvfcVideo | null;
    if (found !== this.video) this.attach(found);
    const v = this.video;
    const now = Date.now();
    let mode: VideoMode = "none";
    if (v) mode = v.srcObject ? "webrtc" : v.currentSrc || v.src ? "mse" : v.poster ? "mjpeg" : "none";

    if (v && (mode === "webrtc" || mode === "mse")) {
      // Fortschritt zusätzlich über Abspielposition und dekodierte Frames prüfen – requestVideoFrameCallback
      // feuert nicht überall zuverlässig (z. B. Hintergrund-Tabs, manche eingebettete Browser)
      const progress = Math.round(v.currentTime * 1000) + (v.getVideoPlaybackQuality?.().totalVideoFrames ?? 0) * 1e9;
      if (progress !== this.lastCount) {
        if (this.lastCount >= 0 && now - this.lastFrameAt > 150) this.frame(now, false);
        this.lastCount = progress;
      }
    }
    if (v && mode === "mse" && v.buffered.length) {
      const gap = (v.buffered.end(v.buffered.length - 1) - v.currentTime) * 1000;
      if (gap >= 0 && gap < 30000) this.mseLat = this.mseLat === null ? gap : this.mseLat * 0.8 + gap * 0.2;
    }
    const delayMs = mode === "webrtc" ? this.rtcLat ?? 0 : mode === "mse" ? this.mseLat ?? 0 : 0;
    const threshold = Math.max(STALL_MIN_MS, this.frameIv * 4);
    const playing = mode === "webrtc" || mode === "mse";
    const stalled = playing && this.lastFrameAt > 0 && now - this.lastFrameAt > threshold;
    clocks.set(this.cameraId, { mode, delayMs, lastFrameAt: this.lastFrameAt, stalled });
  }
}

/** Beobachtet das <video> im umgebenden `.player`-Container des Ankerelements. */
export function useVideoMonitor(cameraId: number, anchor: RefObject<Element | null>) {
  useEffect(() => {
    const m = new VideoMonitor(cameraId, () => anchor.current?.closest(".player")?.querySelector("video") ?? null);
    return () => m.stop();
  }, [cameraId, anchor]);
}

/* ---------------- Auswahl ---------------- */

export interface SyncedFrame {
  objects: DetObject[];
  /** Video hängt (länger als STALL_SHOW_MS) */
  stalled: boolean;
  mode: VideoMode;
  /** gemessene Player-Verzögerung (ms) */
  autoMs: number;
  /** manueller Versatz (ms) */
  manualMs: number;
  /** tatsächlich angewendete Verzögerung (ms) */
  delayMs: number;
}

function lerpObjects(a: DetObject[], b: DetObject[], f: number): DetObject[] {
  if (f <= 0) return a;
  const byId = new Map(b.map((o) => [o.track_id, o]));
  return a.map((o) => {
    const n = byId.get(o.track_id);
    if (!n) return o;
    const bb = o.bbox.map((v, i) => v + (n.bbox[i] - v) * f) as DetObject["bbox"];
    return { ...o, bbox: bb };
  });
}

export function selectFrame(cameraId: number, manualS: number, now = Date.now()): SyncedFrame {
  const clock = clocks.get(cameraId);
  const autoMs = clock?.delayMs ?? 0;
  const manualMs = manualS * 1000;
  const delayMs = Math.max(0, autoMs + manualMs);
  const stallMs = clock?.stalled && clock.lastFrameAt ? now - clock.lastFrameAt : 0;
  // Hängt das Video, bleibt die Overlay-Zeit beim zuletzt gezeigten Bild stehen
  const target = (stallMs ? clock!.lastFrameAt : now) - delayMs;
  const base = { stalled: stallMs > STALL_SHOW_MS, mode: clock?.mode ?? "none", autoMs, manualMs, delayMs } as const;
  const list = buffers.get(cameraId);
  if (!list?.length) return { ...base, objects: [] };
  let i = list.length - 1;
  while (i >= 0 && list[i].t > target) i--;
  if (i < 0 || target - list[i].t > STALE_MS) return { ...base, objects: [] };
  const a = list[i], b = list[i + 1];
  if (!b || b.t - a.t > INTERP_MAX_MS) return { ...base, objects: a.objects };
  return { ...base, objects: lerpObjects(a.objects, b.objects, (target - a.t) / (b.t - a.t)) };
}

/** Synchronisierte Detektionen einer Kamera, neu berechnet alle `intervalMs`. */
export function useSyncedFrame(cameraId: number, intervalMs: number): SyncedFrame {
  const manualS = useUi((s) => s.overlayDelay?.[cameraId] ?? 0);
  const [, tick] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => tick((x) => x + 1), intervalMs);
    return () => window.clearInterval(t);
  }, [intervalMs]);
  return selectFrame(cameraId, manualS);
}
