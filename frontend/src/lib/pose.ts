// W2 (C1/D1): Namen und Hilfen für Haltung und Gesten
import type { GestureName, Posture, VGEvent } from "./types";

import { msgs } from "../i18n";

/** Lokalisierte Namen als Proxy (Zugriff wie auf ein Objekt, Text aus der aktuellen Sprache). */
function localized<K extends string>(pick: () => Record<string, string>): Record<K, string> {
  return new Proxy({} as Record<K, string>, {
    get: (_t, k) => (typeof k === "string" ? pick()[k] : undefined),
    has: (_t, k) => typeof k === "string" && k in pick(),
    ownKeys: () => Reflect.ownKeys(pick()),
    getOwnPropertyDescriptor: (_t, k) =>
      typeof k === "string" && k in pick() ? { enumerable: true, configurable: true, value: pick()[k] } : undefined,
  });
}

export const POSTURE_NAME = localized<Posture>(() => msgs().pose.posture);

/** Auswahl im Regel-Editor („Person … seit mindestens …“) */
export const POSTURE_TRIGGER_NAME = localized<Exclude<Posture, "unknown">>(() => msgs().pose.postureTrigger);

export const GESTURE_NAME = localized<GestureName>(() => msgs().pose.gesture);

export const GESTURE_HINT = localized<GestureName>(() => msgs().pose.gestureHint);

export const ALL_GESTURES: GestureName[] = ["arms_up", "arm_left", "arm_right", "arms_cross"];

/** Dauer kompakt: „45 s“, „1:20 min“, „2 h 5 min“ */
export function fmtDuration(s: number): string {
  const v = Math.max(0, Math.round(s));
  const m = msgs().pose;
  if (v < 60) return m.durSec(v);
  if (v < 3600) return m.durMinSec(Math.floor(v / 60), String(v % 60).padStart(2, "0"));
  return m.durHourMin(Math.floor(v / 3600), Math.floor((v % 3600) / 60));
}

export interface EventAlert {
  rule: string;
  rule_id: number;
  trigger: string;
  repeat: number;
  fired_at: string;
  first_fired_at?: string;
  posture?: Posture;
  posture_since?: string;
}

/** Notfall-Kennzeichnung eines Events (attributes.alert, gesetzt von Haltungs-Regeln oder Regeln mit Wiederholung) */
export function eventAlert(e: Pick<VGEvent, "attributes">): EventAlert | null {
  const a = (e.attributes as Record<string, unknown> | undefined)?.alert;
  return a && typeof a === "object" ? (a as EventAlert) : null;
}
