import type { PetsMessages } from "../en/pets";

/** Namensbereich „pets“ – Deutsch (muss vollständig sein, gleiche Schlüssel und Funktionen wie en). */
const pets = {
  purpose: { food: "Napf", water: "Wasser", litter: "Klo" },
  status: { ok: "normal", low: "weniger als sonst", high: "mehr als sonst", gap: "lange Pause", unknown: "lernt noch" },
  unknownPet: (label: string) => `${label} (unbekannt)`,
  running: "läuft",
  noVisits: "keine Besuche",
  today: "heute",
  normalPerDay: (r: string) => `normal ${r} am Tag`,
  soFar: (r: string) => `bis jetzt ${r}`,
  normalAfter7: "Normalwert ab 7 Tagen Daten",
  lastAt: (t: string, ago: string) => `zuletzt ${t} (${ago})`,
  never: "noch nie",
  usualGap: (g: string) => `sonst max. ${g} Pause`,
  showVisits: "Besuche dieses Tiers anzeigen",
  identity: "Identität",
  todayDay: "Heute",
  yesterday: "Gestern",
  title: "Tiere",
  subtitle: "Haustier-Tagebuch: Napf, Wasser und Katzenklo – heute im Vergleich zu den letzten 14 Tagen",
  barsPeriod: "Zeitraum der Balken",
  nDays: (n: number) => `${n} Tage`,
  noVisitsYet: "Noch keine Besuche",
  emptyText: "Legen Sie in der Kameraverwaltung eine Zone an und wählen Sie als Zweck „Napf“, „Wasser“ oder „Katzenklo“. Besuche von Hunden und Katzen in diesen Zonen erscheinen dann hier (Einstellungen → Haustier-Tagebuch).",
  visits: "Besuche",
  visitsOf: (name: string) => `Besuche – ${name}`,
  total: (n: number) => `${n} insgesamt`,
  allPets: "alle Tiere",
  allPurposes: "alle Zwecke",
  noVisitsList: "Keine Besuche.",
  loadMore: "Weitere laden",
} satisfies PetsMessages;

export default pets;
