import type { PoseMessages } from "../en/pose";

/** Namensbereich „pose“ – Deutsch (muss vollständig sein, gleiche Schlüssel und Funktionen wie en). */
const pose = {
  posture: { lying: "liegt", sitting: "sitzt", standing: "steht", unknown: "unbekannt" },
  postureTrigger: { lying: "liegt am Boden", sitting: "sitzt", standing: "steht" },
  gesture: {
    arms_up: "Beide Arme hoch",
    arm_left: "Linker Arm seitlich",
    arm_right: "Rechter Arm seitlich",
    arms_cross: "Arme über dem Kopf gekreuzt",
  },
  gestureHint: {
    arms_up: "beide Hände über dem Kopf",
    arm_left: "linker Arm der Person waagerecht zur Seite (aus Sicht der Person)",
    arm_right: "rechter Arm der Person waagerecht zur Seite (aus Sicht der Person)",
    arms_cross: "Unterarme über Kopf-/Schulterhöhe zu einem X gekreuzt",
  },
  durSec: (s: number) => `${s} s`,
  durMinSec: (m: number, ss: string) => `${m}:${ss} min`,
  durHourMin: (h: number, m: number) => `${h} h ${m} min`,
} satisfies PoseMessages;

export default pose;
