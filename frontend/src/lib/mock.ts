/**
 * Mock-Modus (VITE_MOCK=1): simuliert Backend-REST und /api/ws im Browser,
 * damit das Frontend ohne laufendes Backend entwickelt werden kann.
 */
import type {
  Camera, Identity, Rule, Sample, Settings, VGEvent, DetObject, IdentityKind,
} from "./types";
import type { InventoryHistoryEntry, InventoryItem, InventoryRelation, InventorySearchHit } from "./types";
import type { ApiToken, TokenScope } from "./types";
import type { IdentitySuggestion } from "./types";
import type { CameraHealth, InventoryChange, InventoryChanges, PushTarget, VGNotification } from "./types";
import type { RuleAction, RuleInput } from "./types";

const now = () => new Date().toISOString();
const ago = (s: number) => new Date(Date.now() - s * 1000).toISOString();

function sceneSvg(title: string, hue: number, w = 1280, h = 720): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="hsl(${hue},25%,22%)"/><stop offset="1" stop-color="hsl(${hue},20%,8%)"/></linearGradient></defs>
<rect width="100%" height="100%" fill="url(#g)"/>
<path d="M0 ${h * 0.62} L${w} ${h * 0.55} L${w} ${h} L0 ${h}Z" fill="hsl(${hue},10%,14%)"/>
<path d="M${w * 0.3} ${h} L${w * 0.45} ${h * 0.58} L${w * 0.55} ${h * 0.58} L${w * 0.8} ${h}Z" fill="hsl(${hue},6%,20%)"/>
<rect x="${w * 0.62}" y="${h * 0.22}" width="${w * 0.3}" height="${h * 0.36}" fill="hsl(${hue},12%,18%)"/>
<rect x="${w * 0.66}" y="${h * 0.3}" width="${w * 0.06}" height="${h * 0.08}" fill="hsl(45,70%,55%)" opacity=".6"/>
<text x="24" y="${h - 24}" font-family="monospace" font-size="22" fill="#ffffff88">${title}</text></svg>`;
  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}

function faceSvg(seed: number, kind: IdentityKind = "face"): string {
  const hue = (seed * 67) % 360;
  const inner =
    kind === "plate"
      ? `<rect x="10" y="40" width="140" height="44" rx="6" fill="#f5f5f5" stroke="#222" stroke-width="3"/><rect x="10" y="40" width="18" height="44" fill="#1e40af"/><text x="88" y="72" text-anchor="middle" font-family="monospace" font-weight="700" font-size="22" fill="#111">B-VG ${100 + seed}</text>`
      : kind === "pet"
        ? `<ellipse cx="80" cy="90" rx="44" ry="38" fill="hsl(${hue},40%,55%)"/><path d="M44 70 L50 30 L70 58Z M116 70 L110 30 L90 58Z" fill="hsl(${hue},40%,45%)"/><circle cx="66" cy="85" r="5" fill="#111"/><circle cx="94" cy="85" r="5" fill="#111"/>`
        : `<circle cx="80" cy="68" r="32" fill="hsl(${hue},35%,62%)"/><path d="M24 160 C24 112 136 112 136 160Z" fill="hsl(${hue},45%,40%)"/>`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160"><rect width="160" height="160" fill="hsl(${hue},20%,18%)"/>${inner}</svg>`;
  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}

// Standardwerte der Inventar-Felder (Gegenstandserkennung) für Mock-Kameras
const INV_OFF = {
  room: null, inventory_enabled: false, inventory_interval_s: 3, inventory_labels: [] as string[],
  inventory_min_score: 0.35, inventory_tiling: false, health_enabled: true,
};

const cameras: Camera[] = [
  { id: 1, name: "Einfahrt", url: "rtsp://testcam:8554/cam1", detect_url: null, enabled: true, detect_fps: 5,
    labels: ["person", "car", "truck", "bicycle", "dog", "cat"], min_score: 0.5,
    face_recognition: true, plate_recognition: true, pet_recognition: true,
    zones: [{ id: "z1", name: "Tor", color: "#ff9800", polygon: [[0.28, 0.6], [0.58, 0.58], [0.82, 0.98], [0.22, 0.98]] }],
    ...INV_OFF, room: "Außen",
    status: { state: "online", fps: 4.9, width: 1280, height: 720, inference_ms: 11.8, last_seen: now() } },
  { id: 2, name: "Garten", url: "rtsp://testcam:8554/cam2", detect_url: null, enabled: true, detect_fps: 5,
    labels: ["person", "dog", "cat", "bird"], min_score: 0.55,
    face_recognition: true, plate_recognition: false, pet_recognition: true,
    zones: [{ id: "z2", name: "Terrasse", color: "#22c55e", polygon: [[0.6, 0.55], [0.95, 0.55], [0.95, 0.95], [0.6, 0.95]] }],
    ...INV_OFF, room: "Garten",
    status: { state: "online", fps: 5.0, width: 1280, height: 720, inference_ms: 12.6, last_seen: now() } },
  { id: 3, name: "Wohnzimmer", url: "rtsp://192.168.1.40/stream1", detect_url: "rtsp://192.168.1.40/stream2", enabled: true, detect_fps: 4,
    labels: ["person", "dog", "cat"], min_score: 0.5, face_recognition: true, plate_recognition: false, pet_recognition: true,
    zones: [], ...INV_OFF, room: "Wohnzimmer", inventory_enabled: true,
    status: { state: "online", fps: 4, width: 2880, height: 1616, inference_ms: 14.1, last_seen: now() } },
];

const identities: Identity[] = [
  { id: 1, kind: "face", name: "Anna", display_name: "Anna", plate_text: null, notes: "Bewohnerin", sample_count: 14, cover_url: faceSvg(1), first_seen: ago(86400 * 20), last_seen: ago(400), event_count: 120, learning_locked: true },
  { id: 2, kind: "face", name: "Paketbote", display_name: "Paketbote", plate_text: null, notes: "", sample_count: 5, cover_url: faceSvg(2), first_seen: ago(86400 * 9), last_seen: ago(7200), event_count: 11, learning_locked: false },
  { id: 12, kind: "face", name: null, display_name: "Person #12", plate_text: null, notes: "", sample_count: 7, cover_url: faceSvg(12), first_seen: ago(86400), last_seen: ago(1800), event_count: 23, learning_locked: false },
  { id: 13, kind: "face", name: null, display_name: "Person #13", plate_text: null, notes: "", sample_count: 2, cover_url: faceSvg(13), first_seen: ago(5000), last_seen: ago(4000), event_count: 3, learning_locked: false },
  { id: 20, kind: "plate", name: "Familienauto", display_name: "Familienauto", plate_text: "B-VG 120", notes: "", sample_count: 9, cover_url: faceSvg(20, "plate"), first_seen: ago(86400 * 30), last_seen: ago(3600), event_count: 54, learning_locked: false },
  { id: 21, kind: "plate", name: null, display_name: "Kennzeichen M-XY 421", plate_text: "M-XY 421", notes: "", sample_count: 3, cover_url: faceSvg(21, "plate"), first_seen: ago(9000), last_seen: ago(8000), event_count: 2, learning_locked: false },
  { id: 30, kind: "pet", name: "Bello", display_name: "Bello", plate_text: null, notes: "Hund der Nachbarn", sample_count: 11, cover_url: faceSvg(30, "pet"), first_seen: ago(86400 * 5), last_seen: ago(600), event_count: 31, learning_locked: false },
  { id: 31, kind: "pet", name: null, display_name: "Tier #3", plate_text: null, notes: "", sample_count: 4, cover_url: faceSvg(31, "pet"), first_seen: ago(86400 * 2), last_seen: ago(20000), event_count: 6, learning_locked: false },
];

/** Mock-Vorschläge: benannte Identitäten gleicher Art, Score deterministisch aus den IDs */
function mockSuggestions(src: Identity, limit: number): IdentitySuggestion[] {
  if (src.kind === "plate") return [];
  const thr = src.kind === "face" ? settings.face_match_threshold : settings.pet_match_threshold;
  return identities.filter((t) => t.kind === src.kind && t.id !== src.id && !!t.name)
    .map((t) => {
      const score = Math.round((src.id === 12 && t.id === 1 ? 0.41 : 0.15 + ((src.id * 7 + t.id * 13) % 20) / 100) * 1000) / 1000;
      return { identity_id: t.id, display_name: t.display_name, name: t.name, cover_url: t.cover_url, score, likely: score >= thr - 0.1 };
    })
    .sort((a, b) => b.score - a.score).slice(0, limit);
}

const samples: Record<number, Sample[]> = {};
for (const i of identities) {
  samples[i.id] = Array.from({ length: Math.min(i.sample_count, 12) }, (_, k) => ({
    id: i.id * 100 + k, image_url: faceSvg(i.id + k * 3, i.kind), created_at: ago(k * 3600),
    quality: Math.round((0.9 - k * 0.04) * 100) / 100, manual: false,
  }));
}

let rules: Rule[] = [
  { id: 1, name: "Person an Einfahrt nachts", enabled: true, camera_ids: [1], zone_ids: ["z1"], labels: ["person"], min_score: 0.6,
    identity: { mode: "unknown", identity_ids: [] }, trigger: "enter", min_dwell_s: 0,
    schedule: { days: [0, 1, 2, 3, 4, 5, 6], start: "22:00", end: "06:00" }, cooldown_s: 30, trigger_options: {}, conditions: [], condition_delay_s: 0,
    actions: [{ type: "snapshot" }, { type: "clip", pre_s: 5, post_s: 10 }, { type: "mqtt", topic: "visionguard/alarm/{camera}", payload: null, retain: false }] },
  { id: 2, name: "Familienauto kommt an", enabled: true, camera_ids: [1], zone_ids: [], labels: ["car"], min_score: 0.5,
    identity: { mode: "specific", identity_ids: [20] }, trigger: "enter", min_dwell_s: 0, schedule: null, cooldown_s: 120, trigger_options: {}, conditions: [], condition_delay_s: 0,
    actions: [{ type: "webhook", url: "http://homeassistant:8123/api/webhook/auto", method: "POST", headers: {}, body: null }] },
  { id: 3, name: "Hund lungert auf Terrasse", enabled: false, camera_ids: [2], zone_ids: ["z2"], labels: ["dog"], min_score: 0.5,
    identity: { mode: "any", identity_ids: [] }, trigger: "dwell", min_dwell_s: 30, schedule: null, cooldown_s: 300, trigger_options: {}, conditions: [], condition_delay_s: 0,
    actions: [{ type: "snapshot" }] },
];

let settings: Settings = {
  retention_days_events: 30, retention_days_media: 7, face_match_threshold: 0.45, pet_match_threshold: 0.8,
  plate_max_distance: 1, mqtt_prefix: "visionguard", publish_all_events: true,
  inventory_vocabulary: [
    { label: "keys", name_de: "Schlüssel", group: "Persönliches", prompts: ["keys", "key ring"], min_score: null },
    { label: "remote", name_de: "Fernbedienung", group: "Elektronik", prompts: ["remote control"], min_score: null },
    { label: "book", name_de: "Buch", group: "Sonstiges", prompts: ["book"], min_score: null },
    { label: "lamp", name_de: "Lampe", group: "Möbel & Einrichtung", prompts: ["lamp"], min_score: null },
    { label: "table", name_de: "Tisch", group: "Möbel & Einrichtung", prompts: ["table", "dining table"], min_score: null },
  ],
  inventory_synonyms: {}, inventory_confirm_n: 2, inventory_miss_m: 3, retention_days_inventory: 30,
  media_link_ttl_h: 24, public_base_url: null,
  push_targets: [
    { id: "handy", type: "ntfy", name: "Handy (ntfy)", enabled: true, server: "https://ntfy.sh", topic: "vg-mock-topic", token: "***", user_key: null, app_token: null },
  ],
  notify_routes: { health: ["handy"], pet: [], review: [], report: [], sensor: [] },
  push_max_per_rule_per_day: 3, weather_location: { lat: 50.11, lon: 8.68 }, retention_days_notifications: 30,
  inventory_change_min_s: 120, health_alert_after_s: 60,
};

/* ---------- W1: Hinweise, Veränderungen, Kamera-Gesundheit ---------- */
let notifications: VGNotification[] = [
  { id: 3, ts: ago(240), source: "rule", level: "info", rule_id: 1, rule: "Schlüssel vergessen", title: "Schlüssel vergessen",
    message: "Schlüssel liegt noch am Brett (Flur, Schlüsselbrett)", camera_id: 1, event_id: null, item_ids: [], image_url: null,
    data: { pushed: true }, delivery: [{ target: "handy", ok: true, status: "sent", error: null, ts: ago(238) }] },
  { id: 2, ts: ago(3600), source: "health", level: "warning", rule_id: null, rule: null, title: "Kamera Garten: unscharf",
    message: "Kamera Garten ist seit 13:02 unscharf (Schärfe 28 % der Referenz).", camera_id: 2, event_id: null, item_ids: [],
    image_url: null, data: { type: "blur", pushed: true }, delivery: [{ target: "handy", ok: false, status: "failed", error: "HTTP 403: forbidden", ts: ago(3598) }] },
  { id: 1, ts: ago(7200), source: "rule", level: "alarm", rule_id: 1, rule: "Medikamente", title: "Medikamente",
    message: "Medikamente heute noch nicht genommen", camera_id: 3, event_id: null, item_ids: [], image_url: null,
    data: { pushed: false }, delivery: [] },
];

function mockChanges(p: URLSearchParams): InventoryChanges {
  // Wie das Backend (B2): neu / weg / verschoben, optional „zwischendurch da“, Hinweis bei unklarem Stand
  const byLabel = (l: string) => inventory.find((i) => i.label === l) ?? inventory[0];
  const snap = (it: InventoryItem, bbox: [number, number, number, number], tsAgo: number, state: InventoryItem["state"] = "present") => ({
    ts: ago(tsAgo), state, camera_id: it.camera_id, bbox, zones: it.zones, zone_names: it.zone_names,
    crop_url: state === "present" ? it.crop_url : null });
  const place = (it: InventoryItem) => it.room ?? it.camera_name;
  const book = byLabel("book"), keys = byLabel("keys"), remote = byLabel("remote"), lamp = byLabel("lamp");
  const changes: InventoryChange[] = [
    { type: "disappeared", at: ago(7200), item: keys, before: snap(keys, keys.bbox, 7300), after: snap(keys, keys.bbox, 7200, "missing"),
      text: `${keys.name_de}: verschwunden (${place(keys)})` },
    { type: "appeared", at: ago(1500), item: book, before: null, after: snap(book, book.bbox, 300),
      text: `${book.name_de}: neu aufgetaucht (${place(book)}), vermutlich auf dem Tisch` },
    { type: "moved", at: ago(2400), item: remote, before: snap(remote, [0.12, 0.6, 0.19, 0.645], 5400), after: snap(remote, remote.bbox, 600),
      text: `${remote.name_de}: vermutlich vom Sofa zum Tisch verschoben (${place(remote)})` },
  ];
  if (p.get("include_transient") === "true" || p.get("include_transient") === "1")
    changes.push({ type: "transient", at: ago(3000), item: lamp, before: null, after: snap(lamp, lamp.bbox, 2950),
      text: `${lamp.name_de}: zwischendurch da, inzwischen wieder weg (${place(lamp)})` });
  if (p.get("since") === "yesterday")   // Beispiel für „Stand unklar“ (Kamera lieferte zeitweise keine Analyse)
    changes.push({ type: "uncertain", at: ago(900), item: { ...lamp, state: "stale", visible: false },
      before: snap(lamp, lamp.bbox, 1000), after: snap(lamp, lamp.bbox, 900, "stale"),
      text: `${lamp.name_de}: Stand unklar – Kamera ${lamp.camera_name} liefert seit ${fmtHm(ago(900))} keine Gegenstandsanalyse` });
  const counts = { appeared: 0, disappeared: 0, moved: 0, uncertain: 0, transient: 0 };
  for (const c of changes) counts[c.type] += 1;
  const sinceLabel: Record<string, string> = { morning: "06:00", today: "00:00", yesterday: "gestern 00:00", "3h": fmtHm(ago(10800)) };
  const parts = [`1 neu (${book.name_de} (${place(book)}))`, `1 weg (${keys.name_de} (${place(keys)}))`, `1 verschoben (${remote.name_de}: Sofa → Tisch)`];
  if (counts.transient) parts.push(`1 zwischendurch da (${lamp.name_de})`);
  const since = p.get("since") ?? "morning";
  return { since: sinceLabel[since] ? since : since, until: now(),
    summary: `Seit ${sinceLabel[since] ?? since}: ${parts.join(", ")}.`, counts, changes };
}

function fmtHm(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function mockHealth(id: number): CameraHealth {
  const warn = id === 2;
  return { camera_id: id, enabled: true,
    current: { state: warn ? "warn" : "ok", problems: warn ? [{ type: "blur", since: ago(3700), text: "Unscharf seit 13:02" }] : [],
      metrics: { brightness: 92, sharpness: warn ? 41 : 160, sharpness_ref: 150, diff: 3.2, inliers: 0.71, shift: 0.01, rotation: 0.2 }, ts: now() },
    reference_urls: { day: null, night: null },
    log: warn ? [{ type: "blur", start_ts: ago(3700), end_ts: null, value: 0.27 }, { type: "dark", start_ts: ago(86400), end_ts: ago(85000), value: 18 }] : [] };
}

/* ---------- Gegenstände (Inventar) ---------- */
type Box4 = [number, number, number, number];
const INV_W = 1440, INV_H = 808; // halbe Auflösung von 2880×1616

/** Wohnzimmer-Szene mit Tischchen, Lampe, Buch und Fernbedienung (Positionen aus den Item-Boxen). */
function livingRoomSvg(items: InventoryItem[]): string {
  const W = INV_W, H = INV_H;
  const box = (b: Box4) => ({ x: b[0] * W, y: b[1] * H, w: (b[2] - b[0]) * W, h: (b[3] - b[1]) * H });
  const parts: string[] = [];
  for (const it of items.filter((i) => i.state === "present")) {
    const r = box(it.bbox);
    // Tischplatte schräg von oben gesehen: Box umfasst Platte und Beine, die Platte liegt im oberen Drittel
    if (it.label === "table") parts.push(`<path d="M${r.x + r.w * 0.06} ${r.y} L${r.x + r.w * 0.94} ${r.y} L${r.x + r.w} ${r.y + r.h * 0.28} L${r.x} ${r.y + r.h * 0.28}Z" fill="#8b5a2b"/><rect x="${r.x}" y="${r.y + r.h * 0.28}" width="${r.w}" height="${r.h * 0.05}" fill="#6d4520"/><rect x="${r.x + r.w * 0.04}" y="${r.y + r.h * 0.33}" width="${r.w * 0.05}" height="${r.h * 0.67}" fill="#6d4520"/><rect x="${r.x + r.w * 0.91}" y="${r.y + r.h * 0.33}" width="${r.w * 0.05}" height="${r.h * 0.67}" fill="#6d4520"/>`);
    if (it.label === "lamp") parts.push(`<path d="M${r.x + r.w * 0.1} ${r.y + r.h * 0.35} L${r.x + r.w * 0.25} ${r.y} L${r.x + r.w * 0.75} ${r.y} L${r.x + r.w * 0.9} ${r.y + r.h * 0.35}Z" fill="#f5deb3"/><rect x="${r.x + r.w * 0.46}" y="${r.y + r.h * 0.35}" width="${r.w * 0.08}" height="${r.h * 0.55}" fill="#aaa"/><ellipse cx="${r.x + r.w / 2}" cy="${r.y + r.h * 0.95}" rx="${r.w * 0.35}" ry="${r.h * 0.05}" fill="#777"/><circle cx="${r.x + r.w / 2}" cy="${r.y + r.h * 0.3}" r="${r.w * 0.9}" fill="#ffd77a" opacity=".08"/>`);
    if (it.label === "book") parts.push(`<rect x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" rx="3" fill="#1d4ed8"/><rect x="${r.x}" y="${r.y + r.h * 0.7}" width="${r.w}" height="${r.h * 0.3}" fill="#f1f5f9"/>`);
    if (it.label === "remote") parts.push(`<rect x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" rx="8" fill="#111827" stroke="#374151" stroke-width="2"/><circle cx="${r.x + r.w * 0.2}" cy="${r.y + r.h / 2}" r="${r.h * 0.18}" fill="#ef4444"/>`);
  }
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
<rect width="100%" height="100%" fill="#2a2420"/><rect y="${H * 0.62}" width="${W}" height="${H * 0.38}" fill="#3b3029"/>
<rect x="${W * 0.78}" y="${H * 0.1}" width="${W * 0.16}" height="${H * 0.36}" fill="#1c2a3a" stroke="#4b3b30" stroke-width="10"/>
${parts.join("")}
<text x="24" y="${H - 24}" font-family="monospace" font-size="22" fill="#ffffff88">Wohnzimmer – cam_3</text></svg>`;
  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}

function itemCropSvg(label: string): string {
  const inner: Record<string, string> = {
    table: `<rect x="20" y="70" width="120" height="14" rx="4" fill="#8b5a2b"/><rect x="28" y="84" width="8" height="56" fill="#6d4520"/><rect x="124" y="84" width="8" height="56" fill="#6d4520"/>`,
    lamp: `<path d="M50 60 L62 22 L98 22 L110 60Z" fill="#f5deb3"/><rect x="76" y="60" width="8" height="70" fill="#aaa"/><ellipse cx="80" cy="134" rx="26" ry="6" fill="#777"/>`,
    book: `<rect x="40" y="40" width="80" height="80" rx="4" fill="#1d4ed8"/><rect x="40" y="100" width="80" height="20" fill="#f1f5f9"/>`,
    remote: `<rect x="62" y="20" width="36" height="120" rx="12" fill="#111827" stroke="#374151" stroke-width="3"/><circle cx="80" cy="40" r="7" fill="#ef4444"/><circle cx="72" cy="66" r="4" fill="#6b7280"/><circle cx="88" cy="66" r="4" fill="#6b7280"/>`,
    keys: `<circle cx="64" cy="64" r="22" fill="none" stroke="#d4d4d8" stroke-width="7"/><rect x="80" y="76" width="54" height="10" rx="3" fill="#eab308" transform="rotate(35 80 76)"/>`,
  };
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160"><rect width="160" height="160" fill="#3b3029"/>${inner[label] ?? `<rect x="50" y="50" width="60" height="60" rx="8" fill="#64748b"/>`}</svg>`;
  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}

const vocabName = (label: string) => settings.inventory_vocabulary.find((v) => v.label === label)?.name_de ?? label;

function mkItem(id: number, label: string, bbox: Box4, state: InventoryItem["state"], lastSeenAgo: number, score: number): InventoryItem {
  const cam = cameras.find((c) => c.id === 3)!;
  return {
    id, camera_id: cam.id, camera_name: cam.name, room: cam.room, label, name_de: vocabName(label), score, bbox,
    zones: [], zone_names: [], state, visible: state === "present", relation: null,
    first_seen: ago(86400 * 3 + id * 600), last_seen: ago(lastSeenAgo), updated_at: ago(lastSeenAgo),
    crop_url: itemCropSvg(label), snapshot_url: null,
  };
}

let inventory: InventoryItem[] = [
  mkItem(1, "table", [0.28, 0.42, 0.74, 0.96], "present", 3, 0.81),
  mkItem(2, "lamp", [0.33, 0.36, 0.41, 0.58], "present", 3, 0.74),
  mkItem(3, "book", [0.48, 0.5, 0.6, 0.57], "present", 3, 0.66),
  mkItem(4, "remote", [0.63, 0.52, 0.7, 0.565], "present", 3, 0.58),
  mkItem(5, "keys", [0.08, 0.66, 0.13, 0.7], "missing", 7200, 0.44),
];

const SURFACES = ["table", "sofa", "bed", "chair", "rug"];
/** Lagebeziehung wie im Backend (2D-Heuristik, vereinfachte Flächenprüfung). */
function relate(it: InventoryItem): InventoryRelation | null {
  if (it.state !== "present" || SURFACES.includes(it.label)) return null;
  const surf = inventory.filter((s) => s.camera_id === it.camera_id && s.state === "present" && SURFACES.includes(s.label));
  const [x1, y1, x2, y2] = it.bbox;
  const area = (x2 - x1) * (y2 - y1) || 1e-6;
  const bx = (x1 + x2) / 2;
  for (const s of surf) {
    const [a1, b1, a2, b2] = s.bbox;
    const ov = Math.max(0, Math.min(x2, a2) - Math.max(x1, a1)) * Math.max(0, Math.min(y2, b2) - Math.max(y1, b1));
    if (bx >= a1 && bx <= a2 && y2 >= b1 && y2 <= b2 && ov / area >= 0.6)
      return { type: "on", item_id: s.id, label: s.label, name_de: s.name_de, text: `vermutlich auf dem ${s.name_de}` };
  }
  for (const s of surf) {
    const [a1, b1, a2, b2] = s.bbox;
    const dx = Math.max(0, a1 - x2, x1 - a2), dy = Math.max(0, b1 - y2, y1 - b2);
    if (Math.hypot(dx, dy) < 0.1)
      return { type: "near", item_id: s.id, label: s.label, name_de: s.name_de, text: `vermutlich neben dem ${s.name_de}` };
  }
  return null;
}
function refreshInventoryMock() {
  // Lagebeziehung in zwei Schritten, damit "on" auch Flächen sieht, deren Zeile weiter hinten steht
  const snap = livingRoomSvg(inventory);
  inventory = inventory.map((i) => ({ ...i, visible: i.state === "present", snapshot_url: snap }));
  inventory = inventory.map((i) => ({ ...i, relation: relate(i) }));
}
refreshInventoryMock();

const normDe = (t: string) => t.toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss")
  .replace(/\?/g, " ").replace(/\b(wo|ist|sind|liegt|der|die|das|den|dem|ein|eine|mein|meine|meinen|unser|unsere)\b/g, " ").trim().replace(/\s+/g, " ");

function searchInventory(q: string): { query: string; answer: string; items: InventorySearchHit[] } {
  const nq = normDe(q);
  const hits: InventorySearchHit[] = [];
  if (nq) {
    for (const it of inventory) {
      const v = settings.inventory_vocabulary.find((x) => x.label === it.label);
      const syn = settings.inventory_synonyms[it.label] ?? [];
      let via: InventorySearchHit["match"]["via"] | null = null;
      if (normDe(it.label) === nq) via = "label";
      else if (normDe(it.name_de).includes(nq) || nq.includes(normDe(it.name_de))) via = "name";
      else if (syn.some((x) => normDe(x).includes(nq))) via = "synonym";
      else if (v?.prompts.some((x) => normDe(x).includes(nq))) via = "prompt";
      if (via) hits.push({ ...it, match: { score: via === "name" || via === "label" ? 100 : 90, via } });
    }
  }
  hits.sort((a, b) => Number(b.visible) - Number(a.visible) || (a.last_seen < b.last_seen ? 1 : -1));
  const h = hits[0];
  const answer = !h ? `Kein Gegenstand „${q.trim()}“ bekannt.`
    : h.visible ? `${h.name_de}: aktuell sichtbar in ${h.room ?? h.camera_name} (Kamera ${h.camera_name})${h.relation ? `, ${h.relation.text}` : ""}, seit ${new Date(h.first_seen).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}.`
    : `${h.name_de}: zuletzt gesehen vor ${Math.max(1, Math.round((Date.now() - new Date(h.last_seen).getTime()) / 3600000))} h in ${h.room ?? h.camera_name} (Kamera ${h.camera_name}).`;
  return { query: q, answer, items: hits };
}

function inventoryHistory(it: InventoryItem): InventoryHistoryEntry[] {
  const list: InventoryHistoryEntry[] = [];
  if (it.state !== "present") list.push({ ts: it.updated_at, state: it.state, camera_id: it.camera_id, bbox: it.bbox, zones: it.zones, score: it.score, crop_url: it.crop_url });
  for (let k = 0; k < 8; k++)
    list.push({ ts: ago((it.state === "present" ? 0 : 7200) + 300 * (k + 1) + it.id * 7), state: "present", camera_id: it.camera_id, bbox: it.bbox, zones: it.zones, score: Math.max(0.3, it.score - k * 0.01), crop_url: it.crop_url });
  list.push({ ts: it.first_seen, state: "present", camera_id: it.camera_id, bbox: it.bbox, zones: it.zones, score: it.score, crop_url: it.crop_url });
  return list;
}

const LABELS = ["person", "person", "person", "car", "car", "dog", "cat", "bicycle", "truck"];
function mkEvent(i: number, ageS: number): VGEvent {
  const label = LABELS[i % LABELS.length];
  const cam = cameras[i % 2];
  const ident =
    label === "person" ? identities[i % 4] : label === "car" ? identities[4 + (i % 2)] : label === "dog" ? identities[6] : null;
  const start = ago(ageS);
  return {
    id: i < 1000 ? `ev-${i}` : `ev-${i}-${Math.random().toString(36).slice(2, 8)}`, camera_id: cam.id, camera_name: cam.name, track_id: 4000 + i,
    label, score: 0.6 + ((i * 37) % 39) / 100, start_ts: start, end_ts: ageS > 30 ? ago(ageS - 12 - (i % 40)) : null,
    zones: i % 3 === 0 ? [cam.zones[0]?.id].filter(Boolean) as string[] : [],
    identity: ident ? { id: ident.id, kind: ident.kind, display_name: ident.display_name, name: ident.name } : null,
    attributes: label === "car" && ident ? { plate_text: ident.plate_text } : {},
    thumb_url: faceSvg(i + 40, label === "car" ? "plate" : label === "dog" || label === "cat" ? "pet" : "face"),
    snapshot_url: i % 4 !== 3 ? sceneSvg(`${cam.name} – Snapshot`, 210 + i * 3) : null,
    clip_url: i % 3 === 0 ? "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4" : null,
    rules: i % 3 === 0 ? [rules[0].name] : [], active: ageS < 30,
  };
}
let events: VGEvent[] = Array.from({ length: 140 }, (_, i) => mkEvent(i, i * 620 + 20));

function json(data: unknown, status = 200): Response {
  return new Response(data === undefined ? null : JSON.stringify(data), {
    status, headers: { "Content-Type": "application/json" },
  });
}

let loggedIn = sessionStorage.getItem("vg.mock.login") === "1";

// API-Zugänge (Mock)
let apiTokens: ApiToken[] = [
  { id: 1, name: "Claude-MCP", prefix: "vgt_ab12cd34", scopes: ["read"], created_at: ago(86400 * 12),
    last_used_at: ago(180), expires_at: null, revoked_at: null, revoked: false, expired: false },
  { id: 2, name: "Home Assistant", prefix: "vgt_9xk2m1qa", scopes: ["read", "write"], created_at: ago(86400 * 40),
    last_used_at: ago(86400 * 2), expires_at: new Date(Date.now() + 86400 * 1000 * 50).toISOString(), revoked_at: null, revoked: false, expired: false },
  { id: 3, name: "Alter Test", prefix: "vgt_old00test", scopes: ["read"], created_at: ago(86400 * 90),
    last_used_at: null, expires_at: null, revoked_at: ago(86400 * 30), revoked: true, expired: false },
];
function mockKey(): { key: string; prefix: string } {
  const a = "abcdefghijklmnopqrstuvwxyz0123456789";
  const b = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  const pick = (al: string, n: number) => Array.from({ length: n }, () => al[Math.floor(Math.random() * al.length)]).join("");
  const short = pick(a, 8);
  return { key: `vgt_${short}_${pick(b, 43)}`, prefix: `vgt_${short}` };
}
let nextId = 100;

function filterEvents(p: URLSearchParams, base = events) {
  let list = base;
  const cam = p.get("camera_id"); if (cam) list = list.filter((e) => String(e.camera_id) === cam);
  const label = p.get("label"); if (label) list = list.filter((e) => e.label === label);
  const iid = p.get("identity_id"); if (iid) list = list.filter((e) => String(e.identity?.id) === iid);
  const since = p.get("since"); if (since) list = list.filter((e) => e.start_ts >= since);
  const until = p.get("until"); if (until) list = list.filter((e) => e.start_ts <= until);
  const active = p.get("active"); if (active) list = list.filter((e) => String(e.active) === active);
  const limit = Number(p.get("limit") ?? 50), offset = Number(p.get("offset") ?? 0);
  return { items: list.slice(offset, offset + limit), total: list.length };
}

async function route(method: string, path: string, p: URLSearchParams, body: any): Promise<Response> {
  await new Promise((r) => setTimeout(r, 120 + Math.random() * 150));
  if (path === "/auth/login") {
    if (body?.username === "admin" && body?.password === "admin") { loggedIn = true; sessionStorage.setItem("vg.mock.login", "1"); return json({ username: "admin" }); }
    return json({ detail: "Benutzername oder Passwort falsch" }, 401);
  }
  if (path === "/auth/logout") { loggedIn = false; sessionStorage.removeItem("vg.mock.login"); return json(undefined, 204); }
  if (!loggedIn) return json({ detail: "Not authenticated" }, 401);
  if (path === "/auth/me") return json({ username: "admin", auth: "session", scopes: ["read", "write"], token_name: null });
  if (path === "/tokens" && method === "GET") return json([...apiTokens].sort((x, y) => y.created_at.localeCompare(x.created_at)));
  if (path === "/tokens" && method === "POST") {
    const name = String(body?.name ?? "").trim();
    if (!name) return json({ detail: "Name darf nicht leer sein" }, 422);
    const scopes: TokenScope[] = (body?.scopes ?? []).includes("write") ? ["read", "write"] : ["read"];
    const { key, prefix } = mockKey();
    const t: ApiToken = { id: nextId++, name, prefix, scopes, created_at: now(), last_used_at: null,
      expires_at: body?.expires_days ? new Date(Date.now() + body.expires_days * 86400 * 1000).toISOString() : null,
      revoked_at: null, revoked: false, expired: false };
    apiTokens.push(t);
    return json({ ...t, token: key }, 201);
  }
  const tokenMatch = method === "DELETE" ? path.match(/^\/tokens\/(\d+)$/) : null;
  if (tokenMatch) {
    const t = apiTokens.find((x) => x.id === Number(tokenMatch[1]));
    if (!t) return json({ detail: "API-Zugang nicht gefunden" }, 404);
    if (!t.revoked) apiTokens = apiTokens.map((x) => (x.id === t.id ? { ...x, revoked: true, revoked_at: now() } : x));
    return json(undefined, 204);
  }
  if (path === "/media/sign") {
    const exp = Math.floor(Date.now() / 1000) + Number(body?.ttl_s ?? 3600);
    const urls: Record<string, string> = {};
    for (const pth of body?.paths ?? []) urls[pth] = `${String(pth).startsWith("/media/") ? pth : `/media/${pth}`}?exp=${exp}&sig=mock`;
    return json({ urls, expires_at: new Date(exp * 1000).toISOString() });
  }

  let m: RegExpMatchArray | null;
  if (path === "/cameras" && method === "GET") return json(cameras);
  if (path === "/cameras" && method === "POST") { const c = { ...body, id: nextId++, status: { state: "starting" } }; cameras.push(c); return json(c, 201); }
  if (path === "/cameras/test") {
    await new Promise((r) => setTimeout(r, 900));
    return json(String(body?.url).includes("fail") ? { ok: false, error: "Verbindung abgelehnt (connection refused)" } : { ok: true, width: 1920, height: 1080, codec: "H264" });
  }
  if ((m = path.match(/^\/cameras\/(\d+)$/))) {
    const id = Number(m[1]); const idx = cameras.findIndex((c) => c.id === id);
    if (idx < 0) return json({ detail: "Nicht gefunden" }, 404);
    if (method === "GET") return json(cameras[idx]);
    if (method === "PUT") {
      const unknown = (body?.inventory_labels ?? []).find((l: string) => !settings.inventory_vocabulary.some((v) => v.label === l));
      if (unknown) return json({ detail: `Unbekannter Gegenstand: ${unknown}` }, 422);
      cameras[idx] = { ...cameras[idx], ...body, id }; return json(cameras[idx]);
    }
    if (method === "DELETE") { cameras.splice(idx, 1); return json(undefined, 204); }
  }
  if (path === "/rules" && method === "GET") return json(rules);
  if (path === "/rules" && method === "POST") { const r = { ...body, id: nextId++ }; rules.push(r); return json(r, 201); }
  if ((m = path.match(/^\/rules\/(\d+)$/))) {
    const id = Number(m[1]);
    if (method === "PUT") { rules = rules.map((r) => (r.id === id ? { ...body, id } : r)); return json(rules.find((r) => r.id === id)); }
    if (method === "DELETE") { rules = rules.filter((r) => r.id !== id); return json(undefined, 204); }
    return json(rules.find((r) => r.id === id));
  }
  if (path === "/events") return json(filterEvents(p));
  if ((m = path.match(/^\/events\/([^/]+)$/))) {
    if (method === "DELETE") { events = events.filter((e) => e.id !== m![1]); return json(undefined, 204); }
    return json(events.find((e) => e.id === m![1]));
  }
  if (path === "/identities") {
    let list = identities;
    const kind = p.get("kind"); if (kind) list = list.filter((i) => i.kind === kind);
    const named = p.get("named"); if (named) list = list.filter((i) => (named === "true") === !!i.name);
    const locked = p.get("locked"); if (locked) list = list.filter((i) => (locked === "true") === i.learning_locked);
    if (p.get("with_suggestion")) list = list.map((i) => ({ ...i, suggestion: mockSuggestions(i, 1).find((x) => x.likely) ?? null }));
    return json(list);
  }
  if ((m = path.match(/^\/identities\/(\d+)(\/.*)?$/))) {
    const id = Number(m[1]); const sub = m[2] ?? ""; const idx = identities.findIndex((i) => i.id === id);
    if (idx < 0) return json({ detail: "Nicht gefunden" }, 404);
    const ident = identities[idx];
    if (sub === "" && method === "GET") return json(ident);
    if (sub === "" && method === "PATCH") {
      if ("name" in body) { ident.name = body.name || null; ident.display_name = ident.name ?? (ident.kind === "plate" ? `Kennzeichen ${ident.plate_text}` : `${ident.kind === "face" ? "Person" : "Tier"} #${id}`); }
      if ("notes" in body) ident.notes = body.notes;
      if (typeof body.learning_locked === "boolean") ident.learning_locked = body.learning_locked;
      return json(ident);
    }
    if (sub === "/suggestions") return json(mockSuggestions(ident, Number(p.get("limit") ?? 3)));
    if (sub === "" && method === "DELETE") { identities.splice(idx, 1); return json(undefined, 204); }
    if (sub === "/merge") {
      const target = identities.find((i) => i.id === body.into_id);
      if (!target) return json({ detail: "Identität nicht gefunden" }, 404);
      if (target.id === id) return json({ detail: "Quelle und Ziel sind identisch" }, 422);
      if (target.kind !== ident.kind) return json({ detail: "Nur Identitäten gleicher Art können zusammengeführt werden" }, 422);
      const src = samples[id] ?? [];
      const ids: number[] | null = Array.isArray(body.sample_ids) ? body.sample_ids : null;
      if (ids && !ids.length) return json({ detail: "Keine Bilder ausgewählt" }, 422);
      if (ids && ids.some((x) => !src.some((s) => s.id === x))) return json({ detail: "Mindestens ein ausgewähltes Bild gehört nicht zur Quelle" }, 422);
      const moving = ids ? src.filter((s) => ids.includes(s.id)).map((s) => ({ ...s, manual: true })) : src;
      const full = !ids || moving.length === src.length;
      const manualAfter = (samples[target.id] ?? []).filter((s) => s.manual).length + moving.filter((s) => s.manual).length;
      if (manualAfter > 20) return json({ detail: "max. 20 Samples – erst alte löschen" }, 422);
      samples[target.id] = [...(samples[target.id] ?? []), ...moving]
        .sort((a, b) => Number(b.manual) - Number(a.manual) || b.quality - a.quality).slice(0, 20);
      target.sample_count = samples[target.id].length;
      if (!full) {
        samples[id] = src.filter((s) => !ids!.includes(s.id)); ident.sample_count = samples[id].length;
        return json(target);
      }
      target.event_count += ident.event_count;
      events = events.map((e) => (e.identity?.id === id ? { ...e, identity: { id: target.id, kind: target.kind, display_name: target.display_name, name: target.name } } : e));
      identities.splice(idx, 1); return json(target);
    }
    if (sub === "/samples") return json(samples[id] ?? []);
    if ((m = sub.match(/^\/samples\/(\d+)$/))) { samples[id] = samples[id].filter((s) => s.id !== Number(m![1])); ident.sample_count--; return json(undefined, 204); }
    if (sub === "/events") return json(filterEvents(p, events.filter((e) => e.identity?.id === id)));
  }
  if (path === "/stats") {
    const hours = Number(p.get("hours") ?? 24);
    const timeline: { ts: string; label: string; count: number }[] = [];
    for (let h = hours - 1; h >= 0; h--) {
      const ts = new Date(Math.floor(Date.now() / 3600000) * 3600000 - h * 3600000).toISOString();
      const hourOfDay = new Date(ts).getHours();
      const day = hourOfDay > 6 && hourOfDay < 21 ? 1 : 0.2;
      timeline.push({ ts, label: "person", count: Math.round((4 + Math.random() * 10) * day) });
      timeline.push({ ts, label: "car", count: Math.round((2 + Math.random() * 6) * day) });
      timeline.push({ ts, label: "dog", count: Math.round(Math.random() * 3 * day) });
      timeline.push({ ts, label: "cat", count: Math.round(Math.random() * 2) });
    }
    const by_label: Record<string, number> = {};
    for (const t of timeline) by_label[t.label] = (by_label[t.label] ?? 0) + t.count;
    return json({ by_label, by_camera: { 1: 142, 2: 87, 3: 0 }, timeline, identities_new: 3 });
  }
  if (path === "/system") {
    return json({
      cameras: cameras.map((c) => ({ id: c.id, name: c.name, status: c.status })),
      worker: { online: true, gpu: { name: "NVIDIA GeForce RTX 4070", util: 30 + Math.round(Math.random() * 20), mem_used: 3120, mem_total: 12282, temp: 54 + Math.round(Math.random() * 5) },
        models: { detector: "yolo11m (TensorRT FP16)", face: "buffalo_l", plate: "fast-plate-ocr", pet: "dinov2-small" } },
      mqtt: { connected: true }, version: "1.0.0-mock",
    });
  }
  if (path === "/inventory/search") { refreshInventoryMock(); return json(searchInventory(p.get("q") ?? "")); }
  if (path === "/inventory/labels") {
    return json(settings.inventory_vocabulary.map((v) => ({
      label: v.label, name_de: v.name_de, group: v.group, prompts: v.prompts, synonyms: settings.inventory_synonyms[v.label] ?? [],
      count_present: inventory.filter((i) => i.label === v.label && i.state === "present").length,
      count_total: inventory.filter((i) => i.label === v.label).length,
    })));
  }
  if (path === "/inventory") {
    refreshInventoryMock();
    let list = inventory;
    const cam = p.get("camera_id"); if (cam) list = list.filter((i) => String(i.camera_id) === cam);
    const room = p.get("room"); if (room) list = list.filter((i) => i.room === room);
    const label = p.get("label"); if (label) list = list.filter((i) => label.split(",").includes(i.label));
    const st = p.get("state"); if (st) list = list.filter((i) => st.split(",").includes(i.state));
    const vis = p.get("visible"); if (vis) list = list.filter((i) => String(i.visible) === vis);
    list = [...list].sort((a, b) => (a.room ?? "").localeCompare(b.room ?? "") || a.name_de.localeCompare(b.name_de) || a.camera_id - b.camera_id);
    const limit = Number(p.get("limit") ?? 200), offset = Number(p.get("offset") ?? 0);
    return json({ items: list.slice(offset, offset + limit), total: list.length });
  }
  if ((m = path.match(/^\/inventory\/(\d+)(\/history)?$/))) {
    const id = Number(m[1]); const it = inventory.find((i) => i.id === id);
    if (!it) return json({ detail: "Gegenstand nicht gefunden" }, 404);
    if (m[2]) return json(inventoryHistory(it).slice(0, Number(p.get("limit") ?? 50)));
    if (method === "DELETE") { inventory = inventory.filter((i) => i.id !== id); return json(undefined, 204); }
    return json(it);
  }
  if (path === "/settings") { if (method === "PUT") settings = { ...settings, ...body }; return json(settings); }
  if (path === "/settings/push_test") {
    const t: PushTarget | undefined = body?.config ?? settings.push_targets.find((x) => x.id === body?.target);
    return t ? json({ ok: t.type === "ntfy", error: t.type === "ntfy" ? null : "Mock: Pushover nicht erreichbar", target: t.id })
      : json({ detail: `Push-Ziel „${body?.target}“ nicht gefunden` }, 404);
  }
  if (path === "/rules/check" && method === "POST") {
    const conds: RuleInput["conditions"] = body?.conditions ?? [];
    const details = conds.map((c) => c.type === "track_came_from"
      ? { type: c.type, ok: null, reason: "wird erst beim Auslösen geprüft (Weg des Tracks)" }
      : c.type === "weather" ? { type: c.type, ok: c.min <= 40, reason: `Wetter: 40 % Regen ab 15 Uhr (Schwelle ${c.min} % in ${c.hours} h)` }
      : c.type === "inventory" ? { type: c.type, ok: c.state === "present", reason: c.state === "present" ? "Schlüssel: sichtbar (Flur, Schlüsselbrett, Score 0.62)" : "Schlüssel: noch sichtbar (Flur, Schlüsselbrett)" }
      : { type: c.type, ok: true, reason: `Medikamente: seit ${c.since} unberührt (Küche, Regal)` });
    const ph = { items: conds.some((c) => c.type.startsWith("inventory")) ? "Schlüssel" : "", item_place: "Flur, Schlüsselbrett", weather: conds.some((c) => c.type === "weather") ? "40 % Regen ab 15 Uhr" : "" };
    const ok = details.every((d) => d.ok !== false);
    const push = (body?.actions ?? []).find((a: RuleAction) => a.type === "push") as Extract<RuleAction, { type: "push" }> | undefined;
    const fill = (s: string) => s.replace(/\{(items|item_place|weather|rule)\}/g, (_x: string, k: string) => (k === "rule" ? body?.name ?? "" : ph[k as keyof typeof ph]));
    return json({ ok, reason: details.map((d) => d.reason).join("; "), details, placeholders: ph, item_ids: ok ? [17] : [],
      preview: push ? { title: fill(push.title), message: fill(push.message) } : null });
  }
  if (path === "/notifications") {
    const src = (p.get("source") ?? "").split(",").filter(Boolean);
    const lvl = (p.get("level") ?? "").split(",").filter(Boolean);
    const list = notifications.filter((n) => (!src.length || src.includes(n.source)) && (!lvl.length || lvl.includes(n.level)));
    const off = Number(p.get("offset") ?? 0), lim = Number(p.get("limit") ?? 50);
    return json({ items: list.slice(off, off + lim), total: list.length });
  }
  if ((m = path.match(/^\/notifications\/(\d+)$/))) {
    const id = Number(m[1]);
    const n = notifications.find((x) => x.id === id);
    if (!n) return json({ detail: "Hinweis nicht gefunden" }, 404);
    if (method === "DELETE") { notifications = notifications.filter((x) => x.id !== id); return json(undefined, 204); }
    return json(n);
  }
  if (path === "/inventory/changes") return json(mockChanges(p));
  if ((m = path.match(/^\/cameras\/(\d+)\/health(\/reference)?$/))) {
    if (m[2]) return json({ request_id: "mock" }, 202);
    return json(mockHealth(Number(m[1])));
  }
  return json({ detail: `Mock: ${method} ${path} nicht implementiert` }, 404);
}

/* ---------- Fake-WebSocket ---------- */
class MockSocket extends EventTarget {
  readyState = 0;
  onopen: ((e: Event) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: ((e: CloseEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  private timers: number[] = [];
  private tracks: Record<number, { id: number; label: string; x: number; y: number; vx: number; vy: number; w: number; h: number; ident: Identity | null; ev: VGEvent }[]> = {};
  private seq = 9000; // >= 1000 -> zufällige IDs

  constructor() {
    super();
    setTimeout(() => { this.readyState = 1; this.onopen?.(new Event("open")); this.start(); }, 300);
  }
  private emit(o: unknown) { this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(o) })); }
  private spawn(camId: number) {
    const cam = cameras.find((c) => c.id === camId)!;
    const label = ["person", "person", "car", "dog"][Math.floor(Math.random() * 4)];
    const isCar = label === "car";
    const w = isCar ? 0.22 : label === "dog" ? 0.08 : 0.07;
    const h = isCar ? 0.16 : label === "dog" ? 0.07 : 0.22;
    const fromLeft = Math.random() > 0.5;
    const ident = label === "person" ? identities[Math.floor(Math.random() * 4)] : isCar ? identities[4] : identities[6];
    const ev = { ...mkEvent(this.seq, 0), camera_id: camId, camera_name: cam.name, label, track_id: this.seq, active: true, start_ts: now(), end_ts: null,
      identity: Math.random() > 0.3 && ident ? { id: ident.id, kind: ident.kind, display_name: ident.display_name, name: ident.name } : null, rules: Math.random() > 0.6 ? [rules[0].name] : [] };
    (this.tracks[camId] ??= []).push({ id: this.seq++, label, x: fromLeft ? -w : 1, y: 0.45 + Math.random() * 0.3, vx: (fromLeft ? 1 : -1) * (0.004 + Math.random() * 0.006), vy: (Math.random() - 0.5) * 0.002, w, h, ident: ev.identity ? ident : null, ev });
    this.emit({ type: "event", action: "new", event: ev });
  }
  private start() {
    this.timers.push(window.setInterval(() => {
      for (const cam of cameras.filter((c) => c.status?.state === "online")) {
        const list = (this.tracks[cam.id] ??= []);
        if (list.length < 3 && Math.random() < 0.02) this.spawn(cam.id);
        for (const t of list) { t.x += t.vx; t.y += t.vy; }
        const gone = list.filter((t) => t.x > 1.05 || t.x < -t.w - 0.05);
        for (const g of gone) { this.emit({ type: "event", action: "end", event: { ...g.ev, active: false, end_ts: now() } }); }
        this.tracks[cam.id] = list.filter((t) => !gone.includes(t));
        const objects: DetObject[] = this.tracks[cam.id].map((t) => ({
          track_id: t.id, label: t.label, score: 0.82 + Math.random() * 0.15,
          bbox: [Math.max(0, t.x), Math.max(0, t.y - t.h), Math.min(1, t.x + t.w), Math.min(1, t.y)],
          zones: [], identity: t.ident ? { id: t.ident.id, kind: t.ident.kind, display_name: t.ident.display_name, name: t.ident.name } : null,
          attributes: t.label === "car" ? { plate_text: "B-VG 120" } : {},
        }));
        this.emit({ type: "detections", camera_id: cam.id, ts: Date.now() / 1000, width: 1280, height: 720, objects });
      }
    }, 200));
    this.timers.push(window.setInterval(() => {
      for (const cam of cameras) if (cam.status?.state === "online")
        this.emit({ type: "camera_status", camera_id: cam.id, status: { ...cam.status, fps: 4.7 + Math.random() * 0.4, inference_ms: 10 + Math.random() * 5, last_seen: now() } });
    }, 5000));
    // Gegenstände: Fernbedienung wandert zwischen Tisch und Boden, das Buch verschwindet ab und zu
    let invStep = 0;
    this.timers.push(window.setInterval(() => {
      invStep++;
      const remote = inventory.find((i) => i.label === "remote");
      const book = inventory.find((i) => i.label === "book");
      let msg: { action: string; items: InventoryItem[] } | null = null;
      if (remote && invStep % 2 === 1) {
        remote.bbox = remote.bbox[1] > 0.6 ? [0.63, 0.52, 0.7, 0.565] : [0.8, 0.88, 0.87, 0.92];
        remote.last_seen = now(); remote.updated_at = now(); remote.score = 0.5 + Math.random() * 0.2;
        refreshInventoryMock();
        msg = { action: "update", items: [inventory.find((i) => i.id === remote.id)!] };
      } else if (book) {
        const wasPresent = book.state === "present";
        book.state = wasPresent ? "missing" : "present";
        if (!wasPresent) book.last_seen = now();
        book.updated_at = now();
        refreshInventoryMock();
        msg = { action: wasPresent ? "gone" : "appear", items: [inventory.find((i) => i.id === book.id)!] };
      }
      if (msg) this.emit({ type: "inventory", camera_id: 3, ...msg });
    }, 15000));
    for (const c of [1, 2]) this.spawn(c);
  }
  send() {}
  close() { this.timers.forEach(clearInterval); this.readyState = 3; this.onclose?.(new CloseEvent("close")); }
}

export function installMock() {
  const realFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, location.origin);
    if (url.pathname.startsWith("/api/")) {
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      return route((init?.method ?? "GET").toUpperCase(), url.pathname.slice(4), url.searchParams, body);
    }
    return realFetch(input, init);
  };
  const RealWS = window.WebSocket;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (window as any).WebSocket = function (url: string, protocols?: string | string[]) {
    if (String(url).includes("/api/ws")) return new MockSocket();
    return new RealWS(url, protocols);
  } as unknown as typeof WebSocket;
  Object.assign(window.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
  // Kamera-Standbilder
  (window as unknown as { __vgMockSnapshot: (id: number) => string }).__vgMockSnapshot = (id: number) => {
    const cam = cameras.find((c) => c.id === id);
    return sceneSvg(cam?.name ?? `Kamera ${id}`, 200 + id * 25);
  };
  console.info("%cVisionGuard Mock-Modus aktiv (admin/admin)", "color:#2dd4bf");
}

export const isMock = import.meta.env.VITE_MOCK === "1";
export function mockSnapshot(id: number): string | null {
  const fn = (window as unknown as { __vgMockSnapshot?: (id: number) => string }).__vgMockSnapshot;
  return fn ? fn(id) : null;
}
