import type { PresenceMessages } from "../en/presence";

/** Namensbereich „presence“ – Deutsch (muss vollständig sein, gleiche Schlüssel und Funktionen wie en). */
const presence = {
  sourceFace: "Gesicht",
  sourcePet: "Tiererkennung",
  sourceReid: "Wiedererkennung",
  sourceNone: "unbekannt",
  unknownWho: (label: string) => `${label} (unbekannt)`,
  camera: (id: number) => `Kamera ${id}`,
  now: "jetzt",
  since: (time: string, dur: string) => ` seit ${time} (${dur})`,
  last: "zuletzt",
  reidHint: "über Kleidung/Statur von einer anderen Kamera übernommen – nur ein Hinweis",
  trailTitle: "Verlauf (Kameras, älteste zuerst)",
  viaReid: (score: string) => ` (Wiedererkennung${score ? ` ${score}` : ""})`,
  viaFace: " (Gesicht)",
  title: "Anwesenheit",
  subtitle: "Wer ist gerade wo – und wer wurde zuletzt wo gesehen (Gesicht, Tiererkennung, Wiedererkennung über Kameras)",
  all: "Alle",
  people: "Personen",
  animals: "Tiere",
  windowTitle: "zuletzt gesehen innerhalb von",
  noReid: "Die Wiedererkennung über Kameras ist an keiner Kamera eingeschaltet – Personen ohne erkanntes Gesicht erscheinen je Kamera getrennt. Einschalten unter Administration → Kameras → „Personenanalyse“.",
  emptyTitle: "Niemand gesehen",
  emptyText: (minutes: number) => `${minutes === 60 ? "In der letzten Stunde" : minutes > 60 ? `In den letzten ${minutes / 60} Stunden` : `In den letzten ${minutes} Minuten`} wurde keine Person und kein Tier erfasst. Die Anwesenheit beginnt nach einem Neustart leer (nur im Arbeitsspeicher).`,
  visibleNow: "Jetzt sichtbar",
  nobodyNow: "Gerade ist niemand zu sehen.",
  lastSeen: "Zuletzt gesehen",
} satisfies PresenceMessages;

export default presence;
