import type { IdentityKind } from "./types";
import { msgs } from "../i18n";

export type LabelGroup = "person" | "vehicle" | "animal" | "other";

export const ALL_LABELS = [
  "person", "car", "truck", "bicycle", "motorcycle", "bus", "dog", "cat", "bird", "horse",
] as const;

const GROUP: Record<string, LabelGroup> = {
  person: "person",
  car: "vehicle", truck: "vehicle", bicycle: "vehicle", motorcycle: "vehicle", bus: "vehicle",
  dog: "animal", cat: "animal", bird: "animal", horse: "animal", sheep: "animal", cow: "animal",
};


export const GROUP_COLOR: Record<LabelGroup, string> = {
  person: "#3b9cff",
  vehicle: "#f59e0b",
  animal: "#22c55e",
  other: "#a78bfa",
};

/** Feinere Farbe pro Label innerhalb der Gruppe (Overlay) */
const LABEL_COLOR: Record<string, string> = {
  person: "#3b9cff",
  car: "#f59e0b", truck: "#fb923c", bus: "#fbbf24", motorcycle: "#f97316", bicycle: "#facc15",
  dog: "#22c55e", cat: "#4ade80", bird: "#2dd4bf", horse: "#84cc16",
};

export const labelGroup = (l: string): LabelGroup => GROUP[l] ?? "other";
/** Anzeigename einer Objektklasse in der aktuellen Sprache (i18n `common.labels`). */
export const labelName = (l: string): string => msgs().common.labels[l] ?? l;
export const labelColor = (l: string): string => LABEL_COLOR[l] ?? GROUP_COLOR[labelGroup(l)];

// Sprachabhängig (i18n `common`): lesen bei jedem Zugriff die aktuelle Sprache.
export const KIND_NAME = new Proxy({} as Record<IdentityKind, string>, {
  get: (_t, k) => msgs().common.kind[k as IdentityKind],
});
export const KIND_PLURAL = new Proxy({} as Record<IdentityKind, string>, {
  get: (_t, k) => msgs().common.kindPlural[k as IdentityKind],
});
/** Wochentage kurz, Montag zuerst (Index 0 = Mo) */
export const DAYS: readonly string[] = new Proxy(Array.from({ length: 7 }, () => ""), {
  get: (t, p, r) => (typeof p === "string" && /^\d+$/.test(p) ? msgs().common.daysShort[Number(p)] : Reflect.get(t, p, r)),
});
