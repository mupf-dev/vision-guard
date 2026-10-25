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
  ItemIdentityInput, LlmTestResult, PetSummary, PetVisit, Report, ReportGenerateInput, ReportList, ReviewAnswerInput,
  ReviewList, ReviewRequest, ReviewStatus, ReviewThreshold, ZonePurpose,
} from "./types";

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
  identity_id?: number;
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
};

/** Antwort von /identities/{id}/events darf laut Vertrag "wie /events" sein – tolerant auch für Arrays. */
export function asPaged<T>(v: Paged<T> | T[] | undefined): Paged<T> {
  if (!v) return { items: [], total: 0 };
  if (Array.isArray(v)) return { items: v, total: v.length };
  return v;
}
