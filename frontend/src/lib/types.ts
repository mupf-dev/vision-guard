export type CameraState = "online" | "offline" | "starting" | "disabled";

export interface CameraStatus {
  state: CameraState;
  fps?: number | null;
  width?: number | null;
  height?: number | null;
  inference_ms?: number | null;
  last_seen?: string | null;
}

export interface Zone {
  id: string;
  name: string;
  color: string;
  polygon: [number, number][];
}

export interface Camera {
  id: number;
  name: string;
  url: string;
  detect_url: string | null;
  enabled: boolean;
  detect_fps: number;
  labels: string[];
  min_score: number;
  face_recognition: boolean;
  plate_recognition: boolean;
  pet_recognition: boolean;
  zones: Zone[];
  status?: CameraStatus | null;
}

export type CameraInput = Omit<Camera, "id" | "status">;

export type IdentityMode = "any" | "known" | "unknown" | "specific";
export type Trigger = "enter" | "leave" | "dwell";

export interface Schedule {
  days: number[];
  start: string;
  end: string;
}

export type RuleAction =
  | { type: "snapshot" }
  | { type: "clip"; pre_s: number; post_s: number }
  | { type: "mqtt"; topic: string; payload: string | null; retain: boolean }
  | { type: "webhook"; url: string; method: string; headers: Record<string, string>; body: string | null };

export interface Rule {
  id: number;
  name: string;
  enabled: boolean;
  camera_ids: number[];
  zone_ids: string[];
  labels: string[];
  min_score: number;
  identity: { mode: IdentityMode; identity_ids: number[] };
  trigger: Trigger;
  min_dwell_s: number;
  schedule: Schedule | null;
  cooldown_s: number;
  actions: RuleAction[];
}

export type RuleInput = Omit<Rule, "id">;

export type IdentityKind = "face" | "plate" | "pet";

export interface Identity {
  id: number;
  kind: IdentityKind;
  name: string | null;
  display_name: string;
  plate_text: string | null;
  notes: string;
  sample_count: number;
  cover_url: string | null;
  first_seen: string | null;
  last_seen: string | null;
  event_count: number;
}

export interface IdentityRef {
  id: number;
  kind: IdentityKind;
  display_name: string;
  name: string | null;
}

export interface Sample {
  id: number;
  image_url: string;
  created_at: string;
}

export interface VGEvent {
  id: string;
  camera_id: number;
  camera_name: string;
  track_id: number;
  label: string;
  score: number;
  start_ts: string;
  end_ts: string | null;
  zones: string[];
  identity: IdentityRef | null;
  attributes: { plate_text?: string | null; [k: string]: unknown };
  thumb_url: string | null;
  snapshot_url: string | null;
  clip_url: string | null;
  rules: string[];
  active: boolean;
}

export interface Paged<T> {
  items: T[];
  total: number;
}

export interface Stats {
  by_label: Record<string, number>;
  by_camera: Record<string, number>;
  timeline: { ts: string; label: string; count: number }[];
  identities_new: number;
}

export interface Gpu {
  name?: string | null;
  util?: number | null;
  mem_used?: number | null;
  mem_total?: number | null;
  temp?: number | null;
}

export interface SystemInfo {
  cameras: { id: number; name: string; status: CameraStatus | null }[];
  worker: { online: boolean; gpu?: Gpu | null; models?: Record<string, unknown> | null };
  mqtt: { connected: boolean };
  version?: string;
}

export interface Settings {
  retention_days_events: number;
  retention_days_media: number;
  face_match_threshold: number;
  pet_match_threshold: number;
  plate_max_distance: number;
  mqtt_prefix: string;
  publish_all_events: boolean;
}

export interface DetObject {
  track_id: number;
  label: string;
  score: number;
  bbox: [number, number, number, number];
  zones: string[];
  identity: IdentityRef | null;
  attributes?: { plate_text?: string | null; [k: string]: unknown };
}

export interface DetectionsMsg {
  type: "detections";
  camera_id: number;
  ts: number;
  width: number;
  height: number;
  objects: DetObject[];
}

export type WsMessage =
  | DetectionsMsg
  | { type: "event"; action: "new" | "update" | "end"; event: VGEvent }
  | { type: "camera_status"; camera_id: number; status: CameraStatus }
  | { type: "identity"; identity: Identity };

export interface CameraTestResult {
  ok: boolean;
  width?: number | null;
  height?: number | null;
  codec?: string | null;
  error?: string | null;
}
