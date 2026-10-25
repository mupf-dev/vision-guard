// W2 (C1/D1): Namen und Hilfen für Haltung und Gesten
import type { GestureName, Posture, VGEvent } from "./types";

export const POSTURE_NAME: Record<Posture, string> = {
  lying: "liegt", sitting: "sitzt", standing: "steht", unknown: "unbekannt",
};

/** Auswahl im Regel-Editor („Person … seit mindestens …“) */
export const POSTURE_TRIGGER_NAME: Record<Exclude<Posture, "unknown">, string> = {
  lying: "liegt am Boden", sitting: "sitzt", standing: "steht",
};

export const GESTURE_NAME: Record<GestureName, string> = {
  arms_up: "Beide Arme hoch",
  arm_left: "Linker Arm seitlich",
  arm_right: "Rechter Arm seitlich",
  arms_cross: "Arme über dem Kopf gekreuzt",
};

export const GESTURE_HINT: Record<GestureName, string> = {
  arms_up: "beide Hände über dem Kopf",
  arm_left: "linker Arm der Person waagerecht zur Seite (aus Sicht der Person)",
  arm_right: "rechter Arm der Person waagerecht zur Seite (aus Sicht der Person)",
  arms_cross: "Unterarme über Kopf-/Schulterhöhe zu einem X gekreuzt",
};

export const ALL_GESTURES: GestureName[] = ["arms_up", "arm_left", "arm_right", "arms_cross"];

/** Dauer kompakt: „45 s“, „1:20 min“, „2 h 5 min“ */
export function fmtDuration(s: number): string {
  const v = Math.max(0, Math.round(s));
  if (v < 60) return `${v} s`;
  if (v < 3600) return `${Math.floor(v / 60)}:${String(v % 60).padStart(2, "0")} min`;
  return `${Math.floor(v / 3600)} h ${Math.floor((v % 3600) / 60)} min`;
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
