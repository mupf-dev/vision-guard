import type { SystemMessages } from "../en/system";

/** Namensbereich „system“ – Deutsch (muss vollständig sein, gleiche Schlüssel und Funktionen wie en). */
const system = {
  unavailable: "Systemstatus nicht verfügbar",
  title: "System",
  sub: (version?: string | null) => `Worker, GPU, Modelle und Verbindungen${version ? ` · Version ${version}` : ""}`,
  worker: "KI-Worker",
  connected: "verbunden",
  unreachable: "nicht erreichbar",
  mqtt: "MQTT-Broker",
  disconnected: "getrennt",
  ok: "OK",
  liveWs: "Live-WebSocket",
  connecting: "verbinde …",
  wsConnecting: "Verbinde",
  wsDisconnected: "Getrennt",
  gpu: "GPU",
  utilization: "Auslastung",
  memory: "Speicher",
  temperature: "Temperatur",
  models: "Modelle",
  noModels: "Keine Modellinformationen.",
  cameras: "Kameras",
  colCamera: "Kamera",
  colStatus: "Status",
  colImage: "Bild",
  colImageTitle: "Kamera-Gesundheit: verdeckt, unscharf, zu dunkel, eingefroren, verschoben",
  colFps: "FPS",
  colInference: "Inferenz",
  colResolution: "Auflösung",
  colLast: "Zuletzt",
  model: {
    detector: "Detektor", face: "Gesicht", plate: "Kennzeichen", pet: "Tier", inventory: "Gegenstände",
    embed: "Exemplar-Abgleich", pose: "Haltung & Gesten", history: "Zeitmaschine", reid: "Wiedererkennung über Kameras",
    vlm: "Frag die Kamera",
  } as Record<string, string>,
  state: { ready: "bereit", idle: "im Leerlauf", off: "aus", loading: "lädt", error: "Fehler", paused: "pausiert", disabled: "aus" },
} satisfies SystemMessages;

export default system;
