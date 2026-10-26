/** Namensbereich „events“ (Englisch = Quelle). */
const events = {
  title: "Events",
  count: (n: string, total: number) => `${n} ${total === 1 ? "event" : "events"}`,
  filtered: " (filtered)",
  grid: "Tiles",
  list: "List",
  camera: "Camera",
  allCameras: "All cameras",
  object: "Object",
  allObjects: "All objects",
  identity: "Identity",
  allIdentities: "All identities",
  from: "From",
  until: "Until",
  media: "Media",
  onlySnapshot: "With snapshot only",
  onlyClip: "With clip only",
  reset: "Reset",
  loadError: "Error while loading",
  emptyTitle: "No events",
  emptyFiltered: "Nothing found for these filters.",
  emptyText: "As soon as objects are detected, they appear here.",
  prev: "Previous",
  next: "Next",
  pageOf: (p: number, n: number) => `Page ${p} of ${n}`,
  colTime: "Time",
  colDuration: "Duration",
  colRules: "Rules",
  colMedia: "Media",
};

export type EventsMessages = typeof events;
export default events;
