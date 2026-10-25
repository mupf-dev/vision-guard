export type CameraState = "online" | "offline" | "starting" | "disabled";

export interface CameraStatus {
  state: CameraState;
  fps?: number | null;
  width?: number | null;
  height?: number | null;
  inference_ms?: number | null;
  last_seen?: string | null;
  /** W1 C5: Kamera-Gesundheit (vom Backend, null = unbekannt/noch keine Prüfung) */
  health?: CameraHealthStatus | null;
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
  /** W1 C5: Bildprüfung (dunkel/unscharf/verdeckt/eingefroren/verschoben); PTZ-Kameras: aus */
  health_enabled: boolean;
  status?: CameraStatus | null;
}

export type CameraInput = Omit<Camera, "id" | "status">;

export type IdentityMode = "any" | "known" | "unknown" | "specific";
/** Registry im Backend (rules.TRIGGERS); W1: time = Regel ohne Track/Event → Notification */
export type Trigger = "enter" | "leave" | "dwell" | "time";

/** trigger_options je Trigger: time → { at: "HH:MM" }; enter/leave/dwell → {} */
export interface TriggerOptions {
  at?: string;
  [k: string]: unknown;
}

/** Zusätzliche Prüfungen einer Regel (alle müssen erfüllt sein), Registry rules.CONDITIONS */
export type RuleCondition =
  | { type: "inventory"; labels: string[]; state: "present" | "absent"; camera_ids: number[]; room: string | null; zone_ids: string[] }
  | { type: "inventory_untouched"; labels: string[]; since: string; camera_ids: number[]; zone_ids: string[] }
  | { type: "weather"; metric: "rain_probability"; min: number; hours: number }
  | { type: "track_came_from"; zone_ids: string[] };

export type PushPriority = "low" | "default" | "high";

export interface Schedule {
  days: number[];
  start: string;
  end: string;
}

export type RuleAction =
  | { type: "snapshot" }
  | { type: "clip"; pre_s: number; post_s: number }
  | { type: "mqtt"; topic: string; payload: string | null; retain: boolean }
  | { type: "webhook"; url: string; method: string; headers: Record<string, string>; body: string | null }
  /** W1: Push an ein Ziel aus Settings.push_targets (target null = alle aktiven Ziele) */
  | { type: "push"; target: string | null; title: string; message: string; priority: PushPriority; image: "snapshot" | "crop" | "none" };

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
  trigger_options: TriggerOptions;
  min_dwell_s: number;
  schedule: Schedule | null;
  cooldown_s: number;
  conditions: RuleCondition[];
  /** Sekunden bis zur Prüfung der Bedingungen (0..600) */
  condition_delay_s: number;
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
  // W1: Hinweise und Push
  push_targets: PushTarget[];
  notify_routes: NotifyRoutes;
  push_max_per_rule_per_day: number;
  /** null = keine Wetterabfrage; PUT mit {} setzt zurück */
  weather_location: WeatherLocation | null;
  retention_days_notifications: number;
  // W1: B2 Veränderungen, C5 Kamera-Gesundheit
  inventory_change_min_s: number;
  health_alert_after_s: number;
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
  | InventoryMsg
  | NotificationMsg;

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

// ---------------------------------------------------------------- W1: Hinweise (Notifications) und Push

export type NotificationSource = "rule" | "health" | "pet" | "review" | "report" | "sensor";
export type NotificationLevel = "info" | "warning" | "alarm";
export type NotifyRouteSource = Exclude<NotificationSource, "rule">;
export type NotifyRoutes = Record<NotifyRouteSource, string[]>;

export interface PushDelivery {
  target: string;
  ok: boolean;
  status: "pending" | "sent" | "failed" | "skipped";
  error: string | null;
  ts: string | null;
}

/** „Notification“ ist im Browser schon belegt – daher VGNotification */
export interface VGNotification {
  id: number;
  ts: string;
  source: NotificationSource;
  level: NotificationLevel;
  rule_id: number | null;
  rule: string | null;
  title: string;
  message: string;
  camera_id: number | null;
  event_id: string | null;
  item_ids: number[];
  image_url: string | null;
  /** quellenspezifische Zusatzdaten (z. B. health: {type}); `pushed` = Push wurde versucht */
  data: Record<string, unknown>;
  delivery: PushDelivery[];
}

export interface NotificationMsg {
  type: "notification";
  action: "new" | "update" | "delete";
  /** bei delete nur { id } */
  notification: VGNotification | { id: number };
}

export interface PushTarget {
  id: string;
  type: "ntfy" | "pushover";
  name: string;
  enabled: boolean;
  server: string | null;
  topic: string | null;
  /** Geheimnisse kommen als "***" zurück; "***" unverändert zurückschicken = Wert behalten */
  token: string | null;
  user_key: string | null;
  app_token: string | null;
}

export interface WeatherLocation {
  lat: number;
  lon: number;
}

export interface PushTestInput {
  target?: string;
  config?: PushTarget;
  title?: string;
  message?: string;
}

export interface PushTestResult {
  ok: boolean;
  error: string | null;
  target: string;
}

/** A1: POST /rules/check – Bedingungen eines Regel-Entwurfs jetzt prüfen (ohne Aktionen) */
export interface RuleCheckResult {
  ok: boolean;
  reason: string;
  /** ok null = erst beim Auslösen prüfbar (track_came_from) */
  details: { type: RuleCondition["type"]; ok: boolean | null; reason: string }[];
  placeholders: { items: string; item_place: string; weather: string };
  item_ids: number[];
  preview: { title: string; message: string } | null;
}

// ---------------------------------------------------------------- W1 B2: Veränderungen im Inventar

export type InventoryChangeType = "appeared" | "disappeared" | "moved" | "uncertain" | "transient";

export interface InventoryChangeSnap {
  ts: string;
  state: InventoryState;
  camera_id: number;
  bbox: [number, number, number, number];
  zones: string[];
  zone_names: string[];
  crop_url: string | null;
}

export interface InventoryChange {
  type: InventoryChangeType;
  at: string;
  item: InventoryItem;
  before: InventoryChangeSnap | null;
  after: InventoryChangeSnap | null;
  text: string;
}

export interface InventoryChanges {
  since: string;
  until: string;
  summary: string;
  counts: Record<InventoryChangeType, number>;
  changes: InventoryChange[];
}

// ---------------------------------------------------------------- W1 C5: Kamera-Gesundheit

export type HealthProblemType = "dark" | "blur" | "covered" | "frozen" | "moved";

export interface CameraHealthStatus {
  state: "ok" | "warn" | "unknown";
  problems: { type: HealthProblemType; since: string; text: string }[];
}

export interface CameraHealthLogEntry {
  type: HealthProblemType;
  start_ts: string;
  end_ts: string | null;
  value: number | null;
}

export interface CameraHealth {
  camera_id: number;
  enabled: boolean;
  current: (CameraHealthStatus & { metrics: Record<string, number | null>; ts: string | null }) | null;
  reference_urls: { day: string | null; night: string | null };
  log: CameraHealthLogEntry[];
}
