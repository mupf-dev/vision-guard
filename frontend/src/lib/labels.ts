import type { IdentityKind } from "./types";

export type LabelGroup = "person" | "vehicle" | "animal" | "other";

export const ALL_LABELS = [
  "person", "car", "truck", "bicycle", "motorcycle", "bus", "dog", "cat", "bird", "horse",
] as const;

const GROUP: Record<string, LabelGroup> = {
  person: "person",
  car: "vehicle", truck: "vehicle", bicycle: "vehicle", motorcycle: "vehicle", bus: "vehicle",
  dog: "animal", cat: "animal", bird: "animal", horse: "animal", sheep: "animal", cow: "animal",
};

const DE: Record<string, string> = {
  person: "Person", car: "Auto", truck: "LKW", bicycle: "Fahrrad", motorcycle: "Motorrad", bus: "Bus",
  dog: "Hund", cat: "Katze", bird: "Vogel", horse: "Pferd", sheep: "Schaf", cow: "Kuh",
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
export const labelName = (l: string): string => DE[l] ?? l;
export const labelColor = (l: string): string => LABEL_COLOR[l] ?? GROUP_COLOR[labelGroup(l)];

export const KIND_NAME: Record<IdentityKind, string> = { face: "Person", plate: "Kennzeichen", pet: "Tier" };
export const KIND_PLURAL: Record<IdentityKind, string> = { face: "Personen", plate: "Kennzeichen", pet: "Tiere" };

export const DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
