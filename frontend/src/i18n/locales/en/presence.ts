/** Namensbereich „presence“ (Englisch = Quelle). */
const presence = {
  sourceFace: "Face",
  sourcePet: "Animal recognition",
  sourceReid: "Re-identification",
  sourceNone: "unknown",
  unknownWho: (label: string) => `${label} (unknown)`,
  camera: (id: number) => `Camera ${id}`,
  now: "now",
  since: (time: string, dur: string) => ` since ${time} (${dur})`,
  last: "last seen",
  reidHint: "taken over from another camera via clothing/build – only an indication",
  trailTitle: "Path (cameras, oldest first)",
  viaReid: (score: string) => ` (re-identification${score ? ` ${score}` : ""})`,
  viaFace: " (face)",
  title: "Presence",
  subtitle: "Who is where right now – and who was last seen where (face, animal recognition, re-identification across cameras)",
  all: "All",
  people: "People",
  animals: "Animals",
  windowTitle: "last seen within",
  noReid: "Re-identification across cameras is not enabled on any camera – people without a recognised face appear separately per camera. Enable it under Administration → Cameras → “Person analysis”.",
  emptyTitle: "Nobody seen",
  emptyText: (minutes: number) => `${minutes === 60 ? "In the last hour" : minutes > 60 ? `In the last ${minutes / 60} hours` : `In the last ${minutes} minutes`} no person or animal was detected. Presence starts empty after a restart (kept in memory only).`,
  visibleNow: "Visible now",
  nobodyNow: "Nobody is visible right now.",
  lastSeen: "Last seen",
};

export type PresenceMessages = typeof presence;
export default presence;
