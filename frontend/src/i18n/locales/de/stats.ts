import type { StatsMessages } from "../en/stats";

/** Namensbereich „stats“ – Deutsch (muss vollständig sein, gleiche Schlüssel und Funktionen wie en). */
const stats = {
  title: "Statistik",
  subtitle: "Erkennungen und neue Identitäten im gewählten Zeitraum",
  sum: "Summe",
  deletedCamera: (id: string) => `Gelöschte Kamera (ID ${id})`,
  table: "Tabelle",
  hours: (h: number) => `${h} h`,
  days: (d: number) => `${d} Tage`,
  hourSuffix: " h",
  unavailable: "Statistik nicht verfügbar",
  detections: "Erkennungen",
  topObject: "Häufigstes Objekt",
  topCamera: "Aktivste Kamera",
  newIdentities: "Neue Identitäten",
  perHourByObject: "Erkennungen pro Stunde nach Objekt",
  noData: "Keine Daten",
  perCamera: "Erkennungen pro Kamera",
  byObject: "Nach Objekt",
  time: "Zeit",
} satisfies StatsMessages;

export default stats;
