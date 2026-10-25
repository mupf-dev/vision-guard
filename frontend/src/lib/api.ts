import type {
  Camera, CameraInput, CameraTestResult, Identity, IdentityKind, Paged, Rule, RuleInput,
  Sample, Settings, Stats, SystemInfo, VGEvent, IdentitySuggestion, MergeInput,
} from "./types";
import type { ApiToken, ApiTokenCreated, ApiTokenInput, AuthMe, MediaSignResult } from "./types";
import type {
  InventoryHistoryEntry, InventoryItem, InventoryLabel, InventorySearchResult,
} from "./types";
import type {
  CameraHealth, HealthReferenceResult, InventoryChanges, NotificationLevel, NotificationSource, PushTestInput, PushTestResult, VGNotification,
} from "./types";
import type { RuleCheckResult } from "./types";
import type {
  BulkDeleteInput, BulkDeleteResult, BulkMergeInput, BulkMergeResult, IdentityMergeList, MergeActor, NightlyReport,
  NightlyReportList, NightlyReportSummary, NightlyStatus, UndoMergeResult,
} from "./types";
import type {
  ItemIdentityInput, LlmTestResult, PetSummary, PetVisit, Report, ReportGenerateInput, ReportList, ReviewAnswerInput,
  ReviewList, ReviewRequest, ReviewStatus, ReviewThreshold, ZonePurpose,
} from "./types";
import type {
  AskInput, AskList, AskResult, HistoryFrameThumb, HistorySearch, HistoryStats, SensorHistoryEntry, SensorInput,
  SensorKind, SensorReference, ShoppingItem, ShoppingList, VirtualSensor,
} from "./types";
import type { Presence, PresenceStatus } from "./types";
import type { PtzInfo, PtzPreset, PtzProbeResult, PtzState } from "./types";
import type { TvProbeResult, TvStatus } from "./types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

function qs(q?: Query): string {
  if (!q) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) {
    if (v === undefined || v === null || v === "") continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

async function request<T>(method: string, path: string, body?: unknown, q?: Query): Promise<T> {
  const res = await fetch(`/api${path}${qs(q)}`, {
    method,
    credentials: "include",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401 && !path.startsWith("/auth/")) onUnauthorized?.();
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (typeof j?.detail === "string") msg = j.detail;
      else if (Array.isArray(j?.detail)) msg = j.detail.map((d: { msg?: string }) => d.msg).join(", ");
      else if (j?.error) msg = String(j.error);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

const get = <T>(p: string, q?: Query) => request<T>("GET", p, undefined, q);
const post = <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {});
const put = <T>(p: string, b: unknown) => request<T>("PUT", p, b);
const patch = <T>(p: string, b: unknown) => request<T>("PATCH", p, b);
const del = <T = void>(p: string) => request<T>("DELETE", p);

/** W2 B4: Datei-Upload (multipart) – gleiche Fehlerbehandlung wie request() */
async function upload<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`/api${path}`, { method: "POST", credentials: "include", body: form });
  if (res.status === 401) onUnauthorized?.();
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (typeof j?.detail === "string") msg = j.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, msg);
  }
  return (await res.json()) as T;
}

export interface ReviewQuery extends Query {
  status?: ReviewStatus | string;
  kind?: string;
  camera_id?: number;
  limit?: number;
  offset?: number;
}

export interface PetVisitQuery extends Query {
  /** 0 = unbekannte Tiere (ohne Identität) */
  identity_id?: number;
  label?: string;
  purpose?: ZonePurpose;
  camera_id?: number;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export interface EventQuery extends Query {
  camera_id?: number | string;
  label?: string;
  identity_id?: number | string;
  since?: string;
  until?: string;
  active?: boolean;
  has_snapshot?: boolean;
  has_clip?: boolean;
  /** W2 C1: nur Notfall-Events (attributes.alert) bzw. quittiert ja/nein */
  alert?: boolean;
  acknowledged?: boolean;
  limit?: number;
  offset?: number;
}

export interface InventoryQuery extends Query {
  camera_id?: number | string;
  room?: string;
  label?: string;
  state?: string;
  visible?: boolean;
  signed?: boolean;
  limit?: number;
  offset?: number;
}

export interface NotificationQuery extends Query {
  /** kommagetrennt möglich */
  source?: NotificationSource | string;
  level?: NotificationLevel | string;
  rule_id?: number;
  camera_id?: number;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export interface InventoryChangesQuery extends Query {
  /** ISO-8601, „3h“, „90m“, „2d“, „today“, „morning“, „yesterday“ */
  since: string;
  until?: string;
  camera_id?: number;
  room?: string;
  label?: string;
  include_transient?: boolean;
  limit?: number;
}

export const api = {
  login: (username: string, password: string) => post<{ username: string }>("/auth/login", { username, password }),
  logout: () => post<void>("/auth/logout"),
  me: () => get<AuthMe>("/auth/me"),

  cameras: () => get<Camera[]>("/cameras"),
  camera: (id: number) => get<Camera>(`/cameras/${id}`),
  createCamera: (c: CameraInput) => post<Camera>("/cameras", c),
  updateCamera: (id: number, c: CameraInput) => put<Camera>(`/cameras/${id}`, c),
  deleteCamera: (id: number) => del(`/cameras/${id}`),
  testCamera: (url: string) => post<CameraTestResult>("/cameras/test", { url }),
  snapshotUrl: (id: number, bust?: number) => `/api/cameras/${id}/snapshot.jpg${bust ? `?t=${bust}` : ""}`,
  // PTZ (ONVIF)
  ptz: (id: number) => get<PtzInfo>(`/cameras/${id}/ptz`),
  ptzProbe: (id: number, b: { onvif_port?: number | null; onvif_user?: string | null; onvif_password?: string | null } = {}) =>
    post<PtzProbeResult>(`/cameras/${id}/ptz/probe`, b),
  ptzMove: (id: number, b: { pan?: number; tilt?: number; zoom?: number; duration_ms?: number }) =>
    post<PtzState>(`/cameras/${id}/ptz/move`, b),
  ptzStop: (id: number) => post<PtzState>(`/cameras/${id}/ptz/stop`, {}),
  ptzGoto: (id: number, preset: string | null, hold_s?: number) =>
    post<PtzState>(`/cameras/${id}/ptz/goto`, { preset, hold_s }),
  ptzSavePreset: (id: number, name: string) => post<PtzPreset>(`/cameras/${id}/ptz/presets`, { name }),
  ptzSetHome: (id: number, name?: string) =>
    post<PtzPreset & { state: PtzState }>(`/cameras/${id}/ptz/home`, name ? { name } : {}),
  // Fernseher (CONTRACT TV.C)
  tvs: () => get<TvStatus[]>("/tv"),
  tvProbe: (description_url: string) => post<TvProbeResult>("/tv/probe", { description_url }),
  tvShow: (id: string, camera_id: number, duration_s = 60) =>
    post<TvStatus>(`/tv/${encodeURIComponent(id)}/show`, { camera_id, duration_s }),
  tvStop: (id: string) => post<TvStatus>(`/tv/${encodeURIComponent(id)}/stop`, {}),
  ptzDeletePreset: (id: number, token: string) => del(`/cameras/${id}/ptz/presets/${encodeURIComponent(token)}`),
  ptzResume: (id: number) => post<PtzState>(`/cameras/${id}/ptz/resume`, {}),
  ptzPause: (id: number, minutes: number) => post<PtzState>(`/cameras/${id}/ptz/pause`, { minutes }),

  rules: () => get<Rule[]>("/rules"),
  createRule: (r: RuleInput) => post<Rule>("/rules", r),
  updateRule: (id: number, r: RuleInput) => put<Rule>(`/rules/${id}`, r),
  deleteRule: (id: number) => del(`/rules/${id}`),

  events: (q: EventQuery) => get<Paged<VGEvent>>("/events", q),
  event: (id: string) => get<VGEvent>(`/events/${id}`),
  deleteEvent: (id: string) => del(`/events/${id}`),

  identities: (q: { kind?: IdentityKind; named?: boolean; locked?: boolean; with_suggestion?: 1 }) => get<Identity[]>("/identities", q),
  identity: (id: number) => get<Identity>(`/identities/${id}`),
  updateIdentity: (id: number, b: { name?: string | null; notes?: string; learning_locked?: boolean }) => patch<Identity>(`/identities/${id}`, b),
  deleteIdentity: (id: number) => del(`/identities/${id}`),
  /** sampleIds null/fehlend = klassischer Merge; echte Teilmenge = nur diese Bilder, Quelle samt Events bleibt */
  mergeIdentity: (id: number, into_id: number, sampleIds?: number[] | null) =>
    post<Identity>(`/identities/${id}/merge`, { into_id, sample_ids: sampleIds ?? null } satisfies MergeInput),
  identitySuggestions: (id: number, limit = 3) => get<IdentitySuggestion[]>(`/identities/${id}/suggestions`, { limit }),
  samples: (id: number) => get<Sample[]>(`/identities/${id}/samples`),
  deleteSample: (id: number, sid: number) => del(`/identities/${id}/samples/${sid}`),
  identityEvents: (id: number, q: EventQuery = {}) => get<Paged<VGEvent>>(`/identities/${id}/events`, q),
  // Identitäten 07 (CONTRACT ID.B/ID.C/ID.H)
  bulkDeleteIdentities: (ids: number[]) => post<BulkDeleteResult>("/identities/bulk-delete", { ids } satisfies BulkDeleteInput),
  bulkMergeIdentities: (ids: number[], into_id: number) =>
    post<BulkMergeResult>("/identities/bulk-merge", { ids, into_id } satisfies BulkMergeInput),
  identityMerges: (q: { identity_id?: number; actor?: MergeActor; undone?: boolean; limit?: number; offset?: number } = {}) =>
    get<IdentityMergeList>("/identities/merges", q),
  undoIdentityMerge: (mergeId: number) => post<UndoMergeResult>(`/identities/merges/${mergeId}/undo`, {}),
  nightlyStatus: () => get<NightlyStatus>("/nightly/status"),
  nightlyReports: (q: { limit?: number; offset?: number } = {}) => get<NightlyReportList>("/nightly/reports", q),
  nightlyReport: (id: number) => get<NightlyReport>(`/nightly/reports/${id}`),
  nightlyRun: (mode: "preview" | "apply" = "preview") => post<NightlyReportSummary>("/nightly/run", { mode }),
  nightlyApply: (id: number, idx: number[] | null = null) => post<NightlyReportSummary>(`/nightly/reports/${id}/apply`, { idx }),

  stats: (hours = 24) => get<Stats>("/stats", { hours }),
  system: () => get<SystemInfo>("/system"),
  settings: () => get<Settings>("/settings"),
  saveSettings: (s: Settings) => put<Settings>("/settings", s),

  // API-Zugänge (nur mit Browser-Anmeldung) und signierte Medien-Links
  tokens: () => get<ApiToken[]>("/tokens"),
  createToken: (b: ApiTokenInput) => post<ApiTokenCreated>("/tokens", b),
  revokeToken: (id: number) => del(`/tokens/${id}`),
  signMedia: (paths: string[], ttl_s = 3600) => post<MediaSignResult>("/media/sign", { paths, ttl_s }),

  // Gegenstände (Inventar)
  inventory: (q: InventoryQuery = {}) => get<Paged<InventoryItem>>("/inventory", q),
  inventoryItem: (id: number) => get<InventoryItem>(`/inventory/${id}`),
  deleteInventoryItem: (id: number) => del(`/inventory/${id}`),
  inventorySearch: (q: string, limit = 20) => get<InventorySearchResult>("/inventory/search", { q, limit }),
  inventoryLabels: () => get<InventoryLabel[]>("/inventory/labels"),
  inventoryHistory: (id: number, limit = 50) => get<InventoryHistoryEntry[]>(`/inventory/${id}/history`, { limit }),

  // W1: Hinweise und Push
  notifications: (q: NotificationQuery = {}) => get<Paged<VGNotification>>("/notifications", q),
  notification: (id: number) => get<VGNotification>(`/notifications/${id}`),
  deleteNotification: (id: number) => del(`/notifications/${id}`),
  pushTest: (b: PushTestInput) => post<PushTestResult>("/settings/push_test", b),
  // A1: Bedingungen eines Regel-Entwurfs jetzt prüfen
  checkRule: (b: RuleInput) => post<RuleCheckResult>("/rules/check", b),
  // W1 B2: Veränderungen
  inventoryChanges: (q: InventoryChangesQuery) => get<InventoryChanges>("/inventory/changes", q),
  // W1 C5: Kamera-Gesundheit
  cameraHealth: (id: number, hours = 168) => get<CameraHealth>(`/cameras/${id}/health`, { hours }),
  setHealthReference: (id: number, mode: "day" | "night" | "auto" = "auto") =>
    post<HealthReferenceResult>(`/cameras/${id}/health/reference`, { mode }),

  // W2 D2: Rückfragen
  reviews: (q: ReviewQuery = {}) => get<ReviewList>("/review", q),
  review: (id: number) => get<ReviewRequest>(`/review/${id}`),
  answerReview: (id: number, b: ReviewAnswerInput) => post<ReviewRequest>(`/review/${id}/answer`, b),
  reviewThresholds: (camera_id?: number) => get<ReviewThreshold[]>("/review/thresholds", { camera_id }),
  // W2 C1: Ereignis quittieren
  ackEvent: (id: string) => post<VGEvent>(`/events/${id}/ack`),
  // W2 B4: Exemplare
  createItemIdentity: (b: ItemIdentityInput) => post<Identity>("/identities", b),
  uploadSample: (id: number, file: Blob, bbox?: [number, number, number, number]) => {
    const fd = new FormData();
    fd.append("file", file, "foto.jpg");
    if (bbox) fd.append("bbox", bbox.map((v) => v.toFixed(4)).join(","));
    return upload<Sample>(`/identities/${id}/samples`, fd);
  },
  sampleFromInventory: (id: number, inventory_item_id: number) =>
    post<Sample>(`/identities/${id}/samples/from-inventory`, { inventory_item_id }),
  // W2 C3: Haustier-Tagebuch
  petVisits: (q: PetVisitQuery = {}) => get<Paged<PetVisit>>("/pets/visits", q),
  petSummary: (days = 14) => get<PetSummary[]>("/pets/summary", { days }),
  // W2 B3: Tagesberichte
  reports: (limit = 30, offset = 0) => get<ReportList>("/reports", { limit, offset }),
  report: (id: number) => get<Report>(`/reports/${id}`),
  deleteReport: (id: number) => del(`/reports/${id}`),
  reportPreview: (since?: string, until?: string) => get<Report>("/reports/preview", { since, until }),
  generateReport: (b: ReportGenerateInput = {}) => post<Report>("/reports/generate", b),
  // W2: LLM-Verbindung testen (gespeicherte Einstellungen)
  llmTest: () => post<LlmTestResult>("/settings/llm_test"),

  // W3 A2: Frag die Kamera
  askCamera: (cameraId: number, b: AskInput) => post<AskResult>(`/cameras/${cameraId}/ask`, b),
  askLog: (q: { camera_id?: number; limit?: number; offset?: number } = {}) => get<AskList>("/ask", q),
  deleteAsk: (id: number) => del(`/ask/${id}`),
  // W3 A3+C4: Sensoren und Einkaufsliste
  sensors: (q: { camera_id?: number; room?: string; kind?: SensorKind; supply?: boolean } = {}) =>
    get<VirtualSensor[]>("/sensors", q),
  sensor: (id: number) => get<VirtualSensor>(`/sensors/${id}`),
  createSensor: (b: SensorInput) => post<VirtualSensor>("/sensors", b),
  updateSensor: (id: number, b: SensorInput) => put<VirtualSensor>(`/sensors/${id}`, b),
  deleteSensor: (id: number) => del(`/sensors/${id}`),
  evaluateSensor: (id: number) => post<VirtualSensor>(`/sensors/${id}/evaluate`),
  sensorHistory: (id: number, limit = 50) => get<SensorHistoryEntry[]>(`/sensors/${id}/history`, { limit }),
  sensorReferences: (id: number) => get<SensorReference[]>(`/sensors/${id}/references`),
  addSensorReference: (id: number, state: SensorReference["state"]) =>
    post<SensorReference>(`/sensors/${id}/references`, { state }),
  deleteSensorReference: (id: number, rid: number) => del(`/sensors/${id}/references/${rid}`),
  shoppingList: (done: "false" | "true" | "all" = "false") => get<ShoppingList>("/shopping-list", { done }),
  addShoppingItem: (name: string, note?: string) => post<ShoppingItem>("/shopping-list", { name, note }),
  updateShoppingItem: (id: number, b: { done?: boolean; name?: string; note?: string }) =>
    patch<ShoppingItem>(`/shopping-list/${id}`, b),
  deleteShoppingItem: (id: number) => del(`/shopping-list/${id}`),
  // W3 B1: Zeitmaschine
  historySearch: (q: { q: string; camera_id?: number; room?: string; since?: string; until?: string; limit?: number }) =>
    get<HistorySearch>("/history/search", q),
  historyFrames: (camera_id: number, around: string, span_min = 10) =>
    get<{ items: HistoryFrameThumb[] }>("/history/frames", { camera_id, around, span_min }),
  historyStats: () => get<HistoryStats>("/history/stats"),
  deleteHistory: (q: { camera_id?: number; before?: string } = {}) =>
    request<{ deleted: number }>("DELETE", "/history", undefined, q),
  // W4 C2: Anwesenheit
  presence: (minutes = 60, label?: string) => get<Presence[]>("/presence", { minutes, label }),
  presenceStatus: () => get<PresenceStatus>("/presence/status"),
  clearPresence: () => del("/presence"),
};

/** Antwort von /identities/{id}/events darf laut Vertrag "wie /events" sein – tolerant auch für Arrays. */
export function asPaged<T>(v: Paged<T> | T[] | undefined): Paged<T> {
  if (!v) return { items: [], total: 0 };
  if (Array.isArray(v)) return { items: v, total: v.length };
  return v;
}
