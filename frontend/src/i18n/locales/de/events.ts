import type { EventsMessages } from "../en/events";

/** Namensbereich „events“ – Deutsch (muss vollständig sein, gleiche Schlüssel und Funktionen wie en). */
const events = {
  title: "Ereignisse",
  count: (n: string, total: number) => `${n} ${total === 1 ? "Ereignis" : "Ereignisse"}`,
  filtered: " (gefiltert)",
  grid: "Kacheln",
  list: "Liste",
  camera: "Kamera",
  allCameras: "Alle Kameras",
  object: "Objekt",
  allObjects: "Alle Objekte",
  identity: "Identität",
  allIdentities: "Alle Identitäten",
  from: "Von",
  until: "Bis",
  media: "Medien",
  onlySnapshot: "Nur mit Snapshot",
  onlyClip: "Nur mit Clip",
  reset: "Zurücksetzen",
  loadError: "Fehler beim Laden",
  emptyTitle: "Keine Ereignisse",
  emptyFiltered: "Für diese Filter wurde nichts gefunden.",
  emptyText: "Sobald Objekte erkannt werden, erscheinen sie hier.",
  prev: "Zurück",
  next: "Weiter",
  pageOf: (p: number, n: number) => `Seite ${p} von ${n}`,
  colTime: "Zeit",
  colDuration: "Dauer",
  colRules: "Regeln",
  colMedia: "Medien",
} satisfies EventsMessages;

export default events;
