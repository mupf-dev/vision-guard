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
  /** W2 C3: Zweck fürs Haustier-Tagebuch */
  purpose?: ZonePurpose | null;
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
  /** W2 C1/D1: Haltung/Sturz bzw. Gesten auswerten */
  pose_enabled: boolean;
  gesture_enabled: boolean;
  /** W2 D2: Schwelle je Gegenstands-Label (überschreibt Vokabular/Kamera-Schwelle) */
  inventory_label_min_scores: Record<string, number>;
  /** W2 D2: unterdrückte Fehlerkennungen (gleiches Label, IoU ≥ 0,5) */
  inventory_suppress: InventorySuppress[];
  /** W3 A2: Cloud-Fallback „Frag die Kamera“ erlaubt (Bild verlässt das Haus) */
  vlm_cloud_allowed: boolean;
  /** W3 B1: Zeitmaschine (Bild-Embeddings + Mini-Vorschau, kein Video) */
  history_enabled: boolean;
  history_interval_s: number;
  history_tiling: boolean;
  /** W4 C2: Personen über Kameras wiedererkennen (Kleidung/Statur, Embeddings nur im RAM) */
  reid_enabled: boolean;
  status?: CameraStatus | null;
}

export type CameraInput = Omit<Camera, "id" | "status">;

export type IdentityMode = "any" | "known" | "unknown" | "specific";
/** Registry im Backend (rules.TRIGGERS); W1: time = Regel ohne Track/Event → Notification */
export type Trigger = "enter" | "leave" | "dwell" | "time" | "posture" | "gesture" | "sensor";

/** trigger_options je Trigger: time → { at: "HH:MM" }; posture → PostureTriggerOptions; gesture →
 *  GestureTriggerOptions; enter/leave/dwell → {} */
export interface TriggerOptions {
  at?: string;
  posture?: Posture;
  max_motion?: number | null;
  exclude_zone_ids?: string[];
  gestures?: GestureName[];
  [k: string]: unknown;
}

/** Zusätzliche Prüfungen einer Regel (alle müssen erfüllt sein), Registry rules.CONDITIONS */
export type RuleCondition =
  | { type: "inventory"; labels: string[]; state: "present" | "absent"; camera_ids: number[]; room: string | null; zone_ids: string[] }
  | { type: "inventory_untouched"; labels: string[]; since: string; camera_ids: number[]; zone_ids: string[] }
  | { type: "weather"; metric: "rain_probability"; min: number; hours: number }
  | { type: "track_came_from"; zone_ids: string[] }
  /** W3 (A3): virtueller Sensor ist im Zustand (für alle Trigger) */
  | { type: "sensor"; sensor_id: number; state: SensorState };

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
  /** W2 C1: erneut auslösen alle repeat_s (0 = aus), höchstens repeat_max-mal (0 = unbegrenzt), bis quittiert */
  repeat_s?: number;
  repeat_max?: number;
}

export type RuleInput = Omit<Rule, "id">;

export type IdentityKind = "face" | "plate" | "pet" | "item";

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
  /** W2 B4: nur bei kind "item" – Vokabular-Schlüssel des Exemplars */
  item_label?: string | null;
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
  /** W2 B4/D2 */
  negative?: boolean;
  source?: "auto" | "merge" | "upload" | "review";
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
  /** W2 C1: quittiert (stoppt Wiederholungen) */
  acknowledged_at?: string | null;
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
  worker: {
    online: boolean; gpu?: Gpu | null; models?: Record<string, unknown> | null;
    /** W3 (K3): Leistungsdaten und aktive GPU-Lease */
    perf?: Record<string, unknown> | null;
    gpu_lease?: GpuLease | null;
  };
  mqtt: { connected: boolean };
  /** W3 (K3): pgvector für die Zeitmaschine */
  db?: { vector: boolean; vector_version: string | null };
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
  // W2: Exemplare und Rückfragen (B4/D2)
  item_match_threshold: number;
  item_min_px: number;
  review_enabled: boolean;
  review_max_per_day: number;
  review_max_open: number;
  review_expire_h: number;
  review_band: number;
  review_auto_tune: boolean;
  // W2: Pose und Gesten (C1/D1)
  gesture_hold_s: number;
  fall_default_dwell_s: number;
  fall_track_grace_s: number;
  // W2: Haustier-Tagebuch (C3)
  pet_diary_labels: string[];
  pet_diary_min_visit_s: Record<ZonePurpose, number>;
  pet_diary_notify: boolean;
  pet_diary_single_pet_fallback: boolean;
  // W2: Tagesbericht (B3)
  report_enabled: boolean;
  report_time: string;
  report_days: number[];
  report_generator: "template" | "llm";
  report_channels: ReportChannels;
  // W2: gemeinsamer LLM-Client (Schlüssel nur per Env)
  llm_provider: LlmProvider;
  /** PUT mit "" setzt auf null zurück */
  llm_base_url: string | null;
  llm_model: string | null;
  llm_timeout_s: number;
  /** read-only: Schlüssel per Env gesetzt? */
  llm_key_configured?: Record<Exclude<LlmProvider, "none">, boolean>;
  // W3: Frag die Kamera (A2)
  vlm_enabled: boolean;
  vlm_model: VlmModel;
  vlm_idle_unload_s: number;
  vlm_need_mb: number;
  /** PUT mit "" setzt auf null zurück; null = Cloud aus */
  vlm_cloud_model: string | null;
  retention_days_ask: number;
  // W3: Sensoren und Vorräte (A3+C4)
  retention_days_sensors: number;
  shopping_list_auto: boolean;
  // W3: Zeitmaschine (B1)
  retention_days_history: number;
  history_min_change: number;
  history_model: "siglip2-b16-256" | "mobileclip2-s0";
  // W4: Person über mehrere Kameras (C2)
  reid_ttl_min: number;
  reid_match_threshold: number;
  reid_assign_identity: boolean;
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
  | NotificationMsg
  | ReviewMsg
  | GestureMsg
  | PetVisitMsg
  | ReportMsg
  | SensorMsg
  | ShoppingListMsg
  | PresenceMsg;

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
  /** W2 B4: zugeordnetes Exemplar */
  identity?: IdentityRef | null;
  identity_score?: number | null;
}

export interface InventorySearchHit extends InventoryItem {
  match: { score: number; via: "label" | "name" | "synonym" | "prompt" | "fuzzy" | "identity" };
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

/** Antwort auf POST /cameras/{id}/health/reference (wartet bis 8 s; ok null = Worker hat noch nicht geantwortet) */
export interface HealthReferenceResult { request_id: string; ok?: boolean | null; error?: string | null; path?: string | null }

export interface CameraHealth {
  camera_id: number;
  enabled: boolean;
  current: (CameraHealthStatus & {
    /** Befunde, die noch unter der Warnschwelle `health_alert_after_s` liegen */
    pending?: { type: HealthProblemType; since: string; text: string }[];
    metrics: Record<string, number | string | null>; ts: string | null;
  }) | null;
  reference_urls: { day: string | null; night: string | null };
  log: CameraHealthLogEntry[];
}

// ---------------------------------------------------------------- W2: Rückfragen (D2) und Exemplare (B4)

export type ReviewKind = "inventory_label" | "inventory_identity" | "identity_match" | (string & {});
export type ReviewStatus = "open" | "yes" | "no" | "skipped" | "expired";

export interface ReviewRequest {
  id: number;
  kind: ReviewKind;
  status: ReviewStatus;
  question: string;
  camera_id: number | null;
  camera_name: string | null;
  label: string | null;
  name_de: string | null;
  inventory_item_id: number | null;
  identity_id: number | null;
  source_identity_id: number | null;
  score: number | null;
  threshold: number | null;
  bbox: [number, number, number, number] | null;
  crop_url: string | null;
  snapshot_url: string | null;
  data: Record<string, unknown>;
  created_at: string;
  answered_at: string | null;
  answer_source: "ui" | "push" | "mcp" | "api" | null;
  expires_at: string | null;
  correct_label: string | null;
}

export interface ReviewList extends Paged<ReviewRequest> {
  open_count: number;
  asked_today: number;
  max_per_day: number;
}

export interface ReviewAnswerInput {
  answer: "yes" | "no" | "skip";
  correct_label?: string | null;
}

export interface ReviewThreshold {
  camera_id: number;
  label: string;
  /** Anzeige-Name aus dem Vokabular */
  name_de?: string;
  base: number;
  current: number;
  auto: boolean;
  yes: number;
  no: number;
}

export interface ReviewMsg {
  type: "review";
  action: "new" | "answered" | "expired";
  review: ReviewRequest;
}

export interface InventorySuppress {
  label: string;
  bbox: [number, number, number, number];
  review_id: number | null;
}

/** POST /identities (nur kind "item") */
export interface ItemIdentityInput {
  kind: "item";
  item_label: string;
  name: string;
}

// ---------------------------------------------------------------- W2: Pose und Gesten (C1/D1)

export type Posture = "standing" | "sitting" | "lying" | "unknown";
export type GestureName = "arms_up" | "arm_left" | "arm_right" | "arms_cross";

export interface PostureTriggerOptions {
  posture: Exclude<Posture, "unknown">;
  max_motion: number | null;
  exclude_zone_ids: string[];
}

export interface GestureTriggerOptions {
  gestures: GestureName[];
}

export interface GestureMsg {
  type: "gesture";
  camera_id: number;
  camera_name?: string;
  track_id: number;
  gesture: GestureName;
  score: number;
  held_s: number;
  ts: number;
  zones: string[];
  identity: IdentityRef | null;
}

// ---------------------------------------------------------------- W2: Haustier-Tagebuch (C3)

export type ZonePurpose = "food" | "water" | "litter";

export interface PetVisit {
  id: number;
  identity: IdentityRef | null;
  label: string;
  camera_id: number;
  camera_name: string;
  zone_id: string;
  zone_name: string;
  purpose: ZonePurpose;
  event_id: string | null;
  start_ts: string;
  end_ts: string | null;
  duration_s: number | null;
  thumb_url: string | null;
}

/** ok = im Normalbereich, low/high = weniger/mehr als normal (bis jetzt hochgerechnet), gap = ungewöhnlich lange
 *  Pause, unknown = noch kein Normalwert (mind. 7 Tage Daten). */
export type PetStatus = "ok" | "low" | "high" | "gap" | "unknown";

export interface PetPurposeStats {
  today: number;
  normal: [number, number] | null;
  last_ts: string | null;
  daily: { day: string; count: number }[];
  // C3 (additiv): Median der Tageszahl, Normalbereich bis zur aktuellen Uhrzeit, Pausen in Sekunden
  median?: number | null;
  expected_now?: [number, number] | null;
  status?: PetStatus;
  gap_s?: number | null;
  usual_gap_s?: number | null;
}

export interface PetSummary {
  identity: IdentityRef | null;
  label: string;
  /** C3 (additiv): Anzeigename (Name/„Tier #N“, ohne Identität „Katze (unbekannt)“) */
  name?: string;
  purposes: Partial<Record<ZonePurpose, PetPurposeStats>>;
}

export interface PetVisitMsg {
  type: "pet_visit";
  action: "new" | "update";
  visit: PetVisit;
}

// ---------------------------------------------------------------- W2: Tagesbericht (B3) und LLM

export type LlmProvider = "none" | "openai_compat" | "anthropic";

export interface ReportChannels {
  mqtt: boolean;
  webhook_url: string | null;
  mail_to: string[];
}

export interface ReportDelivery {
  ok: boolean;
  error?: string | null;
  ts?: string | null;
  /** Zusatzangaben je Kanal (B3): topic, notification_id, targets, status, recipients, note */
  [extra: string]: unknown;
}

/** B3: Fakten eines Berichts (GET /reports/{id}, preview) – Zeiten ISO-8601 UTC */
export interface ReportIdentityFact {
  identity_id: number;
  kind: string;
  name: string | null;
  display_name: string;
  plate_text: string | null;
  label: string;
  count: number;
  first: string | null;
  last: string | null;
  cameras: string[];
  new: boolean;
}

export interface ReportLabelFact {
  label: string;
  name_de: string;
  group: "person" | "vehicle" | "animal" | "other";
  count: number;
  first: string | null;
  last: string | null;
  cameras: { camera_id: number; camera_name: string; count: number }[];
}

export interface ReportInventoryChange {
  type: InventoryChangeType;
  at: string;
  text: string;
  item_id: number;
  label: string;
  name_de: string;
  camera_id: number;
  camera_name: string;
  room: string | null;
  before: { ts: string; state: string; zone_names: string[]; crop_url: string | null } | null;
  after: { ts: string; state: string; zone_names: string[]; crop_url: string | null } | null;
}

export interface ReportFacts {
  version: number;
  period: { start: string; end: string; date: string; weekday: string; hours: number };
  events: ReportLabelFact[];
  people: { events: number; named: ReportIdentityFact[]; unknown_faces: number; new_faces: number; without_face: number; unknown: ReportIdentityFact[] };
  vehicles: { total: number; by_label: ReportLabelFact[]; plates: ReportIdentityFact[]; plates_total: number };
  animals: {
    total: number; by_label: ReportLabelFact[]; named: ReportIdentityFact[]; unnamed: number;
    visits: { identity_id: number | null; pet: string | null; label: string; purpose: string; purpose_de: string; count: number; last: string | null; total_s: number }[];
  };
  rules: { name: string; count: number; first: string | null; last: string | null; snapshot_url: string | null }[];
  inventory: { summary: string; counts: Partial<Record<InventoryChangeType, number>>; changes: ReportInventoryChange[] } | null;
  inventory_error?: string;
  notifications: { by_source: Record<string, number>; items: { ts: string; source: string; source_de: string; level: string; title: string }[] };
  system: { cameras: { id: number; name: string; room: string | null }[]; offline_cameras: { id: number; name: string }[] };
  counts: Record<string, number>;
  sections: { key: string; title: string; text: string }[];
  /** nur bei report_generator "llm": Ergebnis der Plausibilitätsprüfung */
  generation?: { requested: "llm"; ok: boolean; model?: string; provider?: string; ms?: number; error?: string; problems?: string[]; rejected_text?: string };
}

export interface Report {
  /** null bei der Vorschau (nicht gespeichert) */
  id: number | null;
  kind: "daily" | "manual";
  period_start: string;
  period_end: string;
  created_at: string;
  text: string;
  generator: string;
  delivery: Record<string, ReportDelivery>;
  /** nur GET /reports/{id} und preview */
  html?: string;
  facts?: ReportFacts;
}

export type ReportList = Paged<Report>;

export interface ReportGenerateInput {
  since?: string | null;
  until?: string | null;
  deliver?: boolean;
}

export interface ReportMsg {
  type: "report";
  report: Report;
}

export interface LlmTestResult {
  ok: boolean;
  error: string | null;
  text: string | null;
  model: string | null;
  ms: number | null;
}

// ------------------------------------------------------------------ Welle 3 (K3)

/** W3.C: aktive GPU-Lease des Workers (vg/internal/system.gpu_lease) */
export interface GpuLease {
  request_id: string;
  holder: string;
  since: number;
  until: number;
  need_mb: number;
  free_mb: number | null;
}

export type VlmModel = "moondream2-int4" | "smolvlm-500m" | "qwen25vl-3b-nf4";

// --- A2 Frag die Kamera
export type AskEngine = "auto" | "local" | "cloud";
export type AskYesNo = "yes" | "no" | "unknown";

export interface AskInput {
  question: string;
  /** normiert [x1,y1,x2,y2] */
  region?: [number, number, number, number] | null;
  engine?: AskEngine;
}

export interface AskResult {
  id: number;
  camera_id: number;
  camera_name: string;
  question: string;
  region: [number, number, number, number] | null;
  answer: string;
  yes_no: AskYesNo;
  engine: Exclude<AskEngine, "auto">;
  model: string;
  ms: number;
  snapshot_url: string | null;
  ts: string;
  source: "ui" | "api" | "mcp" | "sensor";
}

export type AskList = Paged<AskResult>;

// --- A3+C4 Virtuelle Sensoren und Vorräte
export type SensorKind = "brightness" | "blink" | "reference" | "inventory_count" | "fill" | "vlm";
export type SensorStateSet = "binary" | "level";
export type SensorState = "on" | "off" | "ok" | "low" | "out" | "unknown";

export interface VirtualSensor {
  id: number;
  name: string;
  camera_id: number;
  camera_name: string;
  room: string | null;
  enabled: boolean;
  kind: SensorKind;
  /** Polygon wie Zonen (normiert) */
  region: [number, number][];
  /** je Art: brightness {threshold, invert}, blink {min_std, window_s}, inventory_count {labels, min_count,
   * item? (W4: verify_vlm)}, fill {low_below, out_below}, reference {},
   * vlm {measure?: VlmMeasure (Default yesno), question, item? (count/fill), min_count? (count, 1..3)} */
  params: Record<string, unknown>;
  state_set: SensorStateSet;
  supply: boolean;
  verify_vlm: boolean;
  interval_s: number;
  vlm_max_age_s: number;
  change_threshold: number;
  confirm_n: number;
  state: SensorState;
  value: number | null;
  since: string | null;
  last_eval: string | null;
  last_engine: "cheap" | "local" | "cloud" | null;
  snapshot_url: string | null;
  state_text: string;
  /** W4 (C4-VLM): letzte Antwort des Bildmodells (nur im Speicher des Backends, nach Neustart null) */
  last_answer?: string | null;
}

/** W4 (C4-VLM-Modus): Messart eines Bildmodell-Sensors */
export type VlmMeasure = "yesno" | "count" | "fill";

export type SensorInput = Omit<VirtualSensor,
  "id" | "camera_name" | "room" | "state" | "value" | "since" | "last_eval" | "last_engine" | "snapshot_url" | "state_text" | "last_answer">;

export interface SensorHistoryEntry {
  ts: string;
  state: SensorState;
  value: number | null;
  engine: string | null;
  snapshot_url: string | null;
}

export interface SensorReference {
  id: number;
  state: "on" | "off" | "full" | "half" | "empty";
  image_url: string;
  created_at: string;
}

export interface SensorMsg {
  type: "sensor";
  sensor: VirtualSensor | { id: number; deleted: true };
}

export interface ShoppingItem {
  id: number;
  name: string;
  note: string;
  source: "auto" | "manual" | "mcp";
  sensor_id: number | null;
  created_at: string;
  done_at: string | null;
  done: boolean;
}

export interface ShoppingList {
  items: ShoppingItem[];
  open_count: number;
}

export interface ShoppingListMsg {
  type: "shopping_list";
  action: "new" | "update" | "delete";
  item: ShoppingItem;
}

// --- B1 Zeitmaschine
export interface HistoryEpisode {
  camera_id: number;
  camera_name: string;
  room: string | null;
  start_ts: string;
  end_ts: string;
  best_ts: string;
  /** 0..100 */
  score: number;
  tile_bbox: [number, number, number, number] | null;
  thumb_url: string | null;
  frames: number;
  /** roher Kosinus Text↔Bild (Diagnose) */
  similarity?: number;
}

export interface HistorySearch {
  query: string;
  /** tatsächlich kodierte Texte: Original + ggf. englische Fassung */
  queries?: string[];
  model: string;
  items: HistoryEpisode[];
}

export interface HistoryFrameThumb {
  ts: string;
  last_ts: string;
  thumb_url: string | null;
}

export interface HistoryStats {
  enabled: boolean;
  frames: number;
  vectors: number;
  thumb_bytes: number;
  oldest: string | null;
  by_camera: Record<string, number>;
  /** Setting history_model */
  model?: string;
}

// ---------------------------------------------------------------- W4 C2: Anwesenheit / Person über mehrere Kameras

/** face/pet = eigene Erkennung des aktuellen Tracks, reid = über Kleidung/Statur von einer anderen Kamera
 *  übernommen, none = unbekannt */
export type PresenceSource = "face" | "pet" | "reid" | "none";

export interface PresenceTrail {
  camera_id: number;
  camera_name: string | null;
  since: string | null;
  last_seen: string | null;
  active: boolean;
  event_id: string | null;
  /** Score der ReID-Verknüpfung dieses Tracks (null = nicht per ReID verknüpft) */
  score?: number | null;
  via: "face" | "reid" | "track";
}

export interface Presence {
  presence_key: string;
  label: "person" | "dog" | "cat" | string;
  identity: { id: number; kind: IdentityKind; display_name: string; name: string | null } | null;
  source: PresenceSource;
  /** 0..1 – bei reid der Kosinus-Score der Verknüpfung, bei face/pet 1 */
  confidence: number;
  camera_id: number;
  camera_name: string | null;
  room: string | null;
  zones: string[];
  zone_names: string[];
  active: boolean;
  since: string | null;
  last_seen: string | null;
  event_id: string | null;
  thumb_url: string | null;
  /** letzte Tracks dieses Eintrags (neueste zuerst) */
  trail: PresenceTrail[];
}

export interface PresenceDeleted {
  presence_key: string;
  deleted: true;
  merged_into?: string;
}

export interface PresenceStatus {
  entries: number;
  active: number;
  embeddings: number;
  requests: number;
  linked: number;
  new: number;
  /** anonyme Einträge, die ein Track mit Gesicht übernommen hat (ab Schwelle + 0,1) */
  adopted: number;
  identity_given: number;
  merged: number;
  expired: number;
}

export type PresenceMsg = { type: "presence"; presence: Presence | PresenceDeleted };
