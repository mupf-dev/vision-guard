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
  room: string | null;
  inventory_enabled: boolean;
  inventory_interval_s: number;
  inventory_labels: string[];
  inventory_min_score: number;
  inventory_tiling: boolean;
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
  learning_locked: boolean;
  /** nur bei GET /identities?with_suggestion=1 */
  suggestion?: IdentitySuggestion | null;
}

export interface IdentitySuggestion {
  identity_id: number;
  display_name: string;
  name: string | null;
  cover_url: string | null;
  score: number;
  likely: boolean;
}

export interface MergeInput {
  into_id: number;
  /** null/fehlend = klassischer Merge (alle Samples + Events, Quelle wird gelöscht) */
  sample_ids?: number[] | null;
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
  quality: number;
  manual: boolean;
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
  inventory_vocabulary: InventoryVocabEntry[];
  inventory_synonyms: Record<string, string[]>;
  inventory_confirm_n: number;
  inventory_miss_m: number;
  retention_days_inventory: number;
  media_link_ttl_h: number;
  public_base_url: string | null;
}

export interface InventoryVocabEntry {
  label: string;
  name_de: string;
  group: string;
  prompts: string[];
  min_score: number | null;
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
  | { type: "event"; action: "delete"; event: VGEvent }
  | { type: "identity"; identity: Identity }
  | { type: "identity"; identity: IdentityDeleted }
  | InventoryMsg;

export interface IdentityDeleted {
  id: number;
  kind: IdentityKind;
  deleted: true;
  merged_into?: number;
}

export interface CameraTestResult {
  ok: boolean;
  width?: number | null;
  height?: number | null;
  codec?: string | null;
  error?: string | null;
}

// ---------------------------------------------------------------- API-Zugänge (Bearer-Keys)

export type TokenScope = "read" | "write";

export interface ApiToken {
  id: number;
  name: string;
  prefix: string;
  scopes: TokenScope[];
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  revoked: boolean;
  expired: boolean;
}

/** Antwort auf POST /api/tokens – `token` (Klartext) gibt es nur hier, genau einmal. */
export interface ApiTokenCreated extends ApiToken {
  token: string;
}

export interface ApiTokenInput {
  name: string;
  scopes: TokenScope[];
  expires_days: number | null;
}

export interface AuthMe {
  username: string;
  auth?: "session" | "token";
  scopes?: TokenScope[];
  token_name?: string | null;
}

export interface MediaSignResult {
  urls: Record<string, string>;
  expires_at: string;
}

// ---------------------------------------------------------------- Gegenstände (Inventar)

export type InventoryState = "present" | "missing" | "stale";

export interface InventoryRelation {
  type: "on" | "near";
  item_id: number;
  label: string;
  name_de: string;
  text: string;
}

export interface InventoryItem {
  id: number;
  camera_id: number;
  camera_name: string;
  room: string | null;
  label: string;
  name_de: string;
  score: number;
  bbox: [number, number, number, number];
  zones: string[];
  zone_names: string[];
  state: InventoryState;
  visible: boolean;
  relation: InventoryRelation | null;
  first_seen: string;
  last_seen: string;
  updated_at: string;
  crop_url: string | null;
  snapshot_url: string | null;
  /** z. B. „aktuell sichtbar“, „zuletzt gesehen vor 2 h“ (vom Backend) */
  status_text?: string;
}

export interface InventorySearchHit extends InventoryItem {
  match: { score: number; via: "label" | "name" | "synonym" | "prompt" | "fuzzy" };
}

export interface InventorySearchResult {
  query: string;
  answer: string;
  items: InventorySearchHit[];
}

export interface InventoryLabel {
  label: string;
  name_de: string;
  group: string;
  prompts: string[];
  synonyms: string[];
  count_present: number;
  count_total: number;
}

export interface InventoryHistoryEntry {
  ts: string;
  state: InventoryState;
  camera_id: number;
  bbox: [number, number, number, number];
  zones: string[];
  score: number;
  crop_url: string | null;
}

export interface InventoryMsg {
  type: "inventory";
  camera_id: number;
  action: "appear" | "update" | "gone" | "delete";
  items: InventoryItem[];
}
