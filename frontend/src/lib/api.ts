import type {
  Camera, CameraInput, CameraTestResult, Identity, IdentityKind, Paged, Rule, RuleInput,
  Sample, Settings, Stats, SystemInfo, VGEvent,
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

export const api = {
  login: (username: string, password: string) => post<{ username: string }>("/auth/login", { username, password }),
  logout: () => post<void>("/auth/logout"),
  me: () => get<{ username: string }>("/auth/me"),

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

  identities: (q: { kind?: IdentityKind; named?: boolean }) => get<Identity[]>("/identities", q),
  identity: (id: number) => get<Identity>(`/identities/${id}`),
  updateIdentity: (id: number, b: { name?: string | null; notes?: string }) => patch<Identity>(`/identities/${id}`, b),
  deleteIdentity: (id: number) => del(`/identities/${id}`),
  mergeIdentity: (id: number, into_id: number) => post<Identity>(`/identities/${id}/merge`, { into_id }),
  samples: (id: number) => get<Sample[]>(`/identities/${id}/samples`),
  deleteSample: (id: number, sid: number) => del(`/identities/${id}/samples/${sid}`),
  identityEvents: (id: number, q: EventQuery = {}) => get<Paged<VGEvent>>(`/identities/${id}/events`, q),

  stats: (hours = 24) => get<Stats>("/stats", { hours }),
  system: () => get<SystemInfo>("/system"),
  settings: () => get<Settings>("/settings"),
  saveSettings: (s: Settings) => put<Settings>("/settings", s),
};

/** Antwort von /identities/{id}/events darf laut Vertrag "wie /events" sein – tolerant auch für Arrays. */
export function asPaged<T>(v: Paged<T> | T[] | undefined): Paged<T> {
  if (!v) return { items: [], total: 0 };
  if (Array.isArray(v)) return { items: v, total: v.length };
  return v;
}
