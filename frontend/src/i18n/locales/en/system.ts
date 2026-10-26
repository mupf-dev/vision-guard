/** Namensbereich „system“: Systemseite (pages/admin/System.tsx). */
const system = {
  unavailable: "System status unavailable",
  title: "System",
  sub: (version?: string | null) => `Worker, GPU, models and connections${version ? ` · Version ${version}` : ""}`,
  worker: "AI worker",
  connected: "connected",
  unreachable: "unreachable",
  mqtt: "MQTT broker",
  disconnected: "disconnected",
  ok: "OK",
  liveWs: "Live WebSocket",
  connecting: "connecting …",
  wsConnecting: "Connecting",
  wsDisconnected: "Disconnected",
  gpu: "GPU",
  utilization: "Utilization",
  memory: "Memory",
  temperature: "Temperature",
  models: "Models",
  noModels: "No model information.",
  cameras: "Cameras",
  colCamera: "Camera",
  colStatus: "Status",
  colImage: "Image",
  colImageTitle: "Camera health: covered, blurred, too dark, frozen, moved",
  colFps: "FPS",
  colInference: "Inference",
  colResolution: "Resolution",
  colLast: "Last seen",
  /** Modellnamen (Rohschlüssel vom Worker) */
  model: {
    detector: "Detector", face: "Face", plate: "Licence plate", pet: "Animal", inventory: "Items",
    embed: "Instance matching", face_fullres: "Faces from full resolution", pose: "Posture & gestures", history: "Time machine", reid: "Re-identification across cameras",
    vlm: "Ask the camera",
  } as Record<string, string>,
  /** Modellzustände (Präfix des Rohwerts) */
  state: { ready: "ready", idle: "idle", off: "off", loading: "loading", error: "error", paused: "paused", disabled: "off" },
};

export type SystemMessages = typeof system;
export default system;
