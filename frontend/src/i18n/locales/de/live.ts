import type { LiveMessages } from "../en/live";

/** Namensbereich „live“ – Deutsch (muss vollständig sein, gleiche Schlüssel und Funktionen wie en). */
const live = {
  disabled: "Deaktiviert",
  auto: "Auto",
  focusView: "Fokusansicht",
  showFeed: "Feed einblenden",
  hideFeed: "Feed ausblenden",
  recentEvents: "Letzte Ereignisse",
  liveEvents: "Live-Ereignisse",
  noEventsTitle: "Noch keine Ereignisse",
  noEventsText: "Neue Erkennungen erscheinen hier in Echtzeit.",
  kpiCamerasOnline: "Kameras online",
  kpiPeople: "Personen",
  kpiVehicles: "Fahrzeuge",
  kpiAnimals: "Tiere",
  syncTitle: "Overlay-Verzögerung: Boxen zeitlich an das Video anpassen",
  overlayDelay: "Overlay-Verzögerung",
  syncHelp: (mode: string, s: string) => `Die Boxen werden automatisch um die gemessene Verzögerung des Videos (${mode}: ${s} s) verschoben. Laufen sie dem Bild trotzdem voraus (positiv) oder hinterher (negativ), hier nachkorrigieren.`,
  extraDelay: "Zusätzliche Verzögerung in Sekunden",
  total: (s: string) => `Gesamt: ${s} s`,
  videoStalled: " · Video hängt",
  reset: "Zurücksetzen",
  allCameras: "Alle Kameras",
  lastSeen: (ago: string) => `zuletzt ${ago}`,
  zones: "Zonen",
  showZones: "Zonen anzeigen",
  layout: "Layout",
  layoutTitle: (t: string) => `Layout ${t}`,
  loadingCameras: "Lade Kameras …",
  noCamerasTitle: "Noch keine Kameras",
  noCamerasText: "Lege unter Administration → Kameras deine erste Kamera an.",
  addCamera: "Kamera hinzufügen",
} satisfies LiveMessages;

export default live;
