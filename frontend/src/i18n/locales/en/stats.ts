/** Namensbereich „stats“ (Englisch = Quelle). */
const stats = {
  title: "Statistics",
  subtitle: "Detections and new identities in the selected period",
  sum: "Total",
  deletedCamera: (id: string) => `Deleted camera (ID ${id})`,
  table: "Table",
  hours: (h: number) => `${h} h`,
  days: (d: number) => `${d} days`,
  hourSuffix: " h",
  unavailable: "Statistics not available",
  detections: "Detections",
  topObject: "Most frequent object",
  topCamera: "Most active camera",
  newIdentities: "New identities",
  perHourByObject: "Detections per hour by object",
  noData: "No data",
  perCamera: "Detections per camera",
  byObject: "By object",
  time: "Time",
};

export type StatsMessages = typeof stats;
export default stats;
