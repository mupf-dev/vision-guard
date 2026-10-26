import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, Camera as CamIcon, Clapperboard, Clock, Copy, Globe, Image as ImageIcon, ListChecks, Pencil, Plus, Radio, Repeat, Trash2, Wand2, Workflow, X } from "lucide-react";
import { api } from "../../lib/api";
import type { Camera, GestureName, Identity, Posture, Rule, RuleAction, RuleInput, SensorState, VirtualSensor } from "../../lib/types";
import { ALL_LABELS, DAYS, KIND_NAME, labelColor, labelName } from "../../lib/labels";
import { ChipSelect, ConfirmDialog, Dialog, Empty, LabelIcon, Loading, Range, SearchSelect, Switch } from "../../components/ui";
import { toast } from "../../lib/store";
import { pct } from "../../lib/format";
import { ConditionsBlock, ruleTemplates, SENSOR_STATE_NAME, SensorStatePicker, useConditionSummary, useSensors } from "../../components/RuleConditions";
import { ALL_GESTURES, GESTURE_HINT, GESTURE_NAME, POSTURE_TRIGGER_NAME, fmtDuration } from "../../lib/pose";
import { GestureIcon } from "../../components/GestureIcon";
import { Move, Tv } from "lucide-react";
import { fmtNum, msgs, useMsgs } from "../../i18n";

const PLACEHOLDERS = ["{camera}", "{camera_id}", "{label}", "{score}", "{identity}", "{zone}", "{event_id}", "{rule}", "{time}", "{thumb_url}", "{snapshot_url}", "{clip_url}", "{items}", "{item_place}", "{weather}", "{posture}", "{gesture}", "{repeat}", "{sensor}", "{sensor_state}"];

const EMPTY: RuleInput = {
  name: "", enabled: true, camera_ids: [], zone_ids: [], labels: ["person"], min_score: 0.6,
  identity: { mode: "any", identity_ids: [] }, trigger: "enter", trigger_options: {}, min_dwell_s: 0, schedule: null, cooldown_s: 30,
  conditions: [], condition_delay_s: 0,
  actions: [{ type: "snapshot" }],
  repeat_s: 0, repeat_max: 0,
};

// Namen der Auslöser/Aktionen in der aktuellen Sprache (i18n `rules.trigger` / `rules.action`);
// sensor = W3 (A3): virtueller Sensor wechselt (Regel ohne Ereignis)
const TRIGGER_NAME = new Proxy({} as Record<RuleInput["trigger"], string>, {
  get: (_t, k) => msgs().rules.trigger[k as RuleInput["trigger"]],
});

const ACTION_ICON: Record<RuleAction["type"], React.ReactNode> = {
  snapshot: <ImageIcon size={15} />,
  clip: <Clapperboard size={15} />,
  mqtt: <Radio size={15} />,
  webhook: <Globe size={15} />,
  push: <BellRing size={15} />,
  ptz: <Move size={15} />,
  tv: <Tv size={15} />,
};

const ACTION_META = new Proxy({} as Record<RuleAction["type"], { name: string; icon: React.ReactNode }>, {
  get: (_t, k) => ({ name: msgs().rules.action[k as RuleAction["type"]], icon: ACTION_ICON[k as RuleAction["type"]] }),
  ownKeys: () => Reflect.ownKeys(ACTION_ICON),
  getOwnPropertyDescriptor: (_t, k) => (k in ACTION_ICON ? { enumerable: true, configurable: true } : undefined),
});

function newAction(t: RuleAction["type"]): RuleAction {
  switch (t) {
    case "snapshot": return { type: "snapshot" };
    case "clip": return { type: "clip", pre_s: 5, post_s: 10 };
    case "mqtt": return { type: "mqtt", topic: "visionguard/alarm/{camera}", payload: null, retain: false };
    case "webhook": return { type: "webhook", url: "", method: "POST", headers: {}, body: null };
    case "push": return { type: "push", target: null, title: "{rule}", message: msgs().rules.defaultPushMessage, priority: "default", image: "snapshot" };
    case "ptz": return { type: "ptz", camera_id: null, preset: null, hold_s: 30 };
    case "tv": return { type: "tv", target: null, camera_id: null, duration_s: 60 };
  }
}

function joinOr(items: string[]) {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} ${msgs().rules.or} ${items[items.length - 1]}`;
}

export function ruleSummary(r: RuleInput, cams: Camera[], idents: Identity[], condText = "", sensors: VirtualSensor[] = []): React.ReactNode {
  const m = msgs().rules;
  const who = r.labels.length ? joinOr(r.labels.map(labelName)) : m.anObject;
  const idTxt = r.identity.mode === "known" ? m.idKnown : r.identity.mode === "unknown" ? m.idUnknown
    : r.identity.mode === "specific" ? ` (${joinOr(r.identity.identity_ids.map((id) => idents.find((i) => i.id === id)?.display_name ?? `#${id}`)) || "?"})` : "";
  const zones = cams.flatMap((c) => c.zones).filter((z) => r.zone_ids.includes(z.id)).map((z) => z.name);
  const camNames = r.camera_ids.map((id) => cams.find((c) => c.id === id)?.name ?? `#${id}`);
  const where = zones.length ? <>{m.inZone} <b>{joinOr(zones)}</b></> : camNames.length ? <>{m.onCam} <b>{joinOr(camNames)}</b></> : <>{m.onCam} <b>{m.allCameras}</b></>;
  const gs = (r.trigger_options?.gestures ?? []) as GestureName[];
  const verb = r.trigger === "enter" ? m.verbEnter : r.trigger === "leave" ? m.verbLeave
    : r.trigger === "time" ? m.verbTime(String(r.trigger_options?.at ?? "?"))
    : r.trigger === "posture" ? m.verbPosture(POSTURE_TRIGGER_NAME[(r.trigger_options?.posture ?? "lying") as Exclude<Posture, "unknown">], fmtDuration(r.min_dwell_s), r.trigger_options?.max_motion != null)
    : r.trigger === "gesture" ? m.verbGesture(joinOr(gs.map((g) => m.quote(GESTURE_NAME[g]))) || "?")
    : m.verbDwell(r.min_dwell_s);
  const rep = r.repeat_s ? <span className="muted">{m.repeats(fmtDuration(r.repeat_s), r.repeat_max ?? 0)}</span> : null;
  const sched = r.schedule ? ` · ${r.schedule.days.length === 7 ? m.daily : r.schedule.days.map((d) => DAYS[d]).join(", ")} ${r.schedule.start}–${r.schedule.end}` : "";
  const acts = r.actions.map((a) => ACTION_META[a.type].name);
  const cond = condText ? <> {m.and} <b>{condText}</b>{r.condition_delay_s ? <span className="muted">{m.checkedAfter(r.condition_delay_s)}</span> : null}</> : null;
  if (r.trigger === "sensor") {
    const sid = Number(r.trigger_options?.sensor_id ?? 0);
    const st = String(r.trigger_options?.state ?? "on");
    const sname = sensors.find((x) => x.id === sid)?.name ?? (sid ? `#${sid}` : "?");
    return (
      <span className="rule-summary">
        {m.sensorWhen} <b>{sname}</b> {st === "any" ? m.sensorChanges : <>{m.sensorToPre} <b>{SENSOR_STATE_NAME[st as SensorState] ?? st}</b>{m.sensorToPost ? ` ${m.sensorToPost}` : ""}</>}
        {condText ? <>{m.commaAnd} <b>{condText}</b></> : null}
        <span className="arrow">→</span>{acts.length ? acts.join(", ") : m.notification}
      </span>
    );
  }
  if (r.trigger === "time") {
    const days = r.schedule && r.schedule.days.length < 7 ? r.schedule.days.map((d) => DAYS[d]).join(", ") : m.dailyCap;
    return (
      <span className="rule-summary">
        {days} {m.at} <b>{r.trigger_options?.at ?? "?"}</b>{condText ? <>{m.commaIf} <b>{condText}</b></> : null}
        <span className="arrow">→</span>{acts.length ? acts.join(", ") : m.notification}
      </span>
    );
  }
  return (
    <span className="rule-summary">
      {m.when} <b>{who}{idTxt}</b> {m.verbBeforeWhere ? <>{verb} {where}</> : <>{where} {verb}</>}{cond}
      <span className="arrow">→</span>{acts.length ? acts.join(", ") : <span className="muted">{m.noAction}</span>}
      {sched && <span className="muted">{sched}</span>}
      {rep}
    </span>
  );
}

function TemplateInput({ value, onChange, placeholder, multiline }: { value: string; onChange: (v: string) => void; placeholder?: string; multiline?: boolean }) {
  const ref = useRef<HTMLInputElement & HTMLTextAreaElement>(null);
  const m = useMsgs().rules;
  const insert = (p: string) => {
    const el = ref.current;
    const s = el?.selectionStart ?? value.length, e = el?.selectionEnd ?? value.length;
    onChange(value.slice(0, s) + p + value.slice(e));
    requestAnimationFrame(() => { el?.focus(); el?.setSelectionRange(s + p.length, s + p.length); });
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {multiline ? <textarea ref={ref} className="textarea" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
        : <input ref={ref} className="input mono" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />}
      <div className="placeholders">{PLACEHOLDERS.map((p) => <code key={p} title={m.insert} onClick={() => insert(p)}>{p}</code>)}</div>
    </div>
  );
}

function HeadersInput({ value, onChange }: { value: Record<string, string>; onChange: (v: Record<string, string>) => void }) {
  const [text, setText] = useState(Object.keys(value).length ? JSON.stringify(value, null, 2) : "");
  const [err, setErr] = useState<string | null>(null);
  const m = useMsgs().rules;
  return (
    <div className="field"><label>{m.headersJson}</label>
      <textarea className="textarea" style={{ minHeight: 64 }} value={text} placeholder={'{"Authorization": "Bearer …"}'}
        onChange={(e) => {
          setText(e.target.value);
          if (!e.target.value.trim()) { setErr(null); onChange({}); return; }
          try {
            const j = JSON.parse(e.target.value);
            if (typeof j !== "object" || Array.isArray(j) || j === null) throw new Error(m.objectExpected);
            setErr(null); onChange(Object.fromEntries(Object.entries(j).map(([k, v]) => [k, String(v)])));
          } catch (x) { setErr((x as Error).message); }
        }} />
      {err && <span className="hint" style={{ color: "var(--bad)" }}>{m.invalidJson(err)}</span>}
    </div>
  );
}

/** W1 (K1): Aktion „Push“ – Ziele aus Einstellungen → Benachrichtigungen */
function PushActionFields({ a, onChange }: { a: Extract<RuleAction, { type: "push" }>; onChange: (a: RuleAction) => void }) {
  const st = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const targets = st.data?.push_targets ?? [];
  const m = useMsgs().rules;
  return (
    <div className="form-grid">
      <div className="field"><label>{m.target}</label>
        <select className="select" value={a.target ?? ""} onChange={(e) => onChange({ ...a, target: e.target.value || null })}>
          <option value="">{m.allActiveTargets}</option>
          {targets.map((t) => <option key={t.id} value={t.id}>{t.name}{t.enabled ? "" : m.disabledSuffix}</option>)}
        </select>
        {!targets.length && <span className="hint">{m.noPushTargets}</span>}
      </div>
      <div className="field"><label>{m.priorityImage}</label>
        <div className="row">
          <select className="select" value={a.priority} onChange={(e) => onChange({ ...a, priority: e.target.value as typeof a.priority })}>
            <option value="low">{m.prioLow}</option><option value="default">{m.prioDefault}</option><option value="high">{m.prioHigh}</option>
          </select>
          <select className="select" value={a.image} onChange={(e) => onChange({ ...a, image: e.target.value as typeof a.image })}>
            <option value="snapshot">{m.imgSnapshot}</option><option value="crop">{m.imgCrop}</option><option value="none">{m.imgNone}</option>
          </select>
        </div>
      </div>
      <div className="field span-2"><label>{m.pushTitle}</label><TemplateInput value={a.title} onChange={(v) => onChange({ ...a, title: v })} /></div>
      <div className="field span-2"><label>{m.pushText}</label><TemplateInput multiline value={a.message} onChange={(v) => onChange({ ...a, message: v })} /></div>
    </div>
  );
}

/** PTZ: Kamera zu einer gespeicherten Position fahren und dort halten (danach Grundposition). */
function PtzActionFields({ a, onChange }: { a: Extract<RuleAction, { type: "ptz" }>; onChange: (a: RuleAction) => void }) {
  const cams = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const ptzCams = (cams.data ?? []).filter((c) => c.ptz_enabled);
  const camId = a.camera_id ?? (ptzCams.length === 1 ? ptzCams[0].id : null);
  const info = useQuery({ queryKey: ["ptz", camId], queryFn: () => api.ptz(camId!), enabled: camId !== null, staleTime: 30_000 });
  const presets = info.data?.presets ?? [];
  const m = useMsgs().rules;
  if (cams.data && !ptzCams.length) {
    return <div className="hint" style={{ fontSize: 13, color: "var(--warn)" }}>{m.noPtzCams}</div>;
  }
  return (
    <div className="form-grid">
      <div className="field"><label>{m.camera}</label>
        <select className="select" value={a.camera_id ?? ""} onChange={(e) => onChange({ ...a, camera_id: e.target.value ? Number(e.target.value) : null })}>
          <option value="">{m.eventCamera}</option>
          {ptzCams.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>
      <div className="field"><label>{m.position}</label>
        <select className="select" value={a.preset ?? ""} onChange={(e) => onChange({ ...a, preset: e.target.value || null })}>
          <option value="">{m.homePosition}</option>
          {a.preset && !presets.some((p) => p.token === a.preset) && <option value={a.preset}>#{a.preset}</option>}
          {presets.map((p) => <option key={p.token} value={p.token}>{p.name}</option>)}
        </select>
      </div>
      <div className="field span-2"><label>{m.holdThere}</label>
        <Range value={a.hold_s} min={5} max={600} step={5} format={(v) => (v < 120 ? `${v} s` : `${Math.round(v / 60)} min`)} onChange={(v) => onChange({ ...a, hold_s: v })} />
      </div>
      <div className="hint muted span-2" style={{ fontSize: 12 }}>
        {m.ptzHint}
      </div>
    </div>
  );
}

/** Fernseher: Livebild per DLNA im Vollbild zeigen (CONTRACT TV.D). */
function TvActionFields({ a, onChange }: { a: Extract<RuleAction, { type: "tv" }>; onChange: (a: RuleAction) => void }) {
  const cams = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const tvs = useQuery({ queryKey: ["tvs"], queryFn: api.tvs, staleTime: 30_000 });
  const m = useMsgs().rules;
  if (tvs.data && !tvs.data.length) {
    return <div className="hint" style={{ fontSize: 13, color: "var(--warn)" }}>{m.noTv}</div>;
  }
  return (
    <div className="form-grid">
      <div className="field"><label>{m.tv}</label>
        <select className="select" value={a.target ?? ""} onChange={(e) => onChange({ ...a, target: e.target.value || null })}>
          <option value="">{m.allActive}</option>
          {a.target && !(tvs.data ?? []).some((t) => t.id === a.target) && <option value={a.target}>{a.target}{m.unknownSuffix}</option>}
          {(tvs.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}{t.enabled ? "" : m.offSuffix}</option>)}
        </select>
      </div>
      <div className="field"><label>{m.camera}</label>
        <select className="select" value={a.camera_id ?? ""} onChange={(e) => onChange({ ...a, camera_id: e.target.value ? Number(e.target.value) : null })}>
          <option value="">{m.eventCamera}</option>
          {(cams.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>
      <div className="field span-2"><label>{m.showFor}</label>
        <Range value={a.duration_s} min={10} max={600} step={10} format={(v) => (v < 120 ? `${v} s` : `${Math.round(v / 60)} min`)} onChange={(v) => onChange({ ...a, duration_s: v })} />
      </div>
      <div className="hint muted span-2" style={{ fontSize: 12 }}>
        {m.tvHint}
      </div>
    </div>
  );
}

function ActionEditor({ a, onChange, onRemove }: { a: RuleAction; onChange: (a: RuleAction) => void; onRemove: () => void }) {
  const meta = ACTION_META[a.type];
  const m = useMsgs().rules;
  return (
    <div className="action-card">
      <div className="ah"><span className="ico">{meta.icon}</span>{meta.name}<span className="spacer" />
        <button type="button" className="btn btn-ghost btn-icon btn-sm" onClick={onRemove} title={m.removeAction}><X size={15} /></button>
      </div>
      {a.type === "snapshot" && <div className="muted" style={{ fontSize: 13 }}>{m.snapshotDesc}</div>}
      {a.type === "push" && <PushActionFields a={a} onChange={onChange} />}
      {a.type === "ptz" && <PtzActionFields a={a} onChange={onChange} />}
      {a.type === "tv" && <TvActionFields a={a} onChange={onChange} />}
      {a.type === "clip" && (
        <div className="form-grid">
          <div className="field"><label>{m.preRoll}</label><Range value={a.pre_s} min={0} max={30} step={1} format={(v) => `${v} s`} onChange={(v) => onChange({ ...a, pre_s: v })} /></div>
          <div className="field"><label>{m.postRoll}</label><Range value={a.post_s} min={1} max={60} step={1} format={(v) => `${v} s`} onChange={(v) => onChange({ ...a, post_s: v })} /></div>
        </div>
      )}
      {a.type === "mqtt" && (
        <div className="form-grid">
          <div className="field span-2"><label>{m.topic}</label><TemplateInput value={a.topic} onChange={(v) => onChange({ ...a, topic: v })} /></div>
          <div className="field span-2"><label>{m.payloadLabel}</label><TemplateInput multiline value={a.payload ?? ""} placeholder={m.emptyFullEvent} onChange={(v) => onChange({ ...a, payload: v || null })} /></div>
          <Switch checked={a.retain} onChange={(v) => onChange({ ...a, retain: v })} label={m.retain} />
        </div>
      )}
      {a.type === "webhook" && (
        <div className="form-grid">
          <div className="field span-2"><label>{m.url}</label><TemplateInput value={a.url} placeholder="https://example.org/hook?cam={camera}" onChange={(v) => onChange({ ...a, url: v })} /></div>
          <div className="field"><label>{m.method}</label>
            <select className="select" value={a.method} onChange={(e) => onChange({ ...a, method: e.target.value })}>
              {["POST", "PUT", "PATCH", "GET"].map((x) => <option key={x}>{x}</option>)}
            </select>
          </div>
          <div />
          <div className="span-2"><HeadersInput value={a.headers} onChange={(h) => onChange({ ...a, headers: h })} /></div>
          <div className="field span-2"><label>{m.bodyLabel}</label><TemplateInput multiline value={a.body ?? ""} placeholder={m.emptyFullEvent} onChange={(v) => onChange({ ...a, body: v || null })} /></div>
        </div>
      )}
    </div>
  );
}

/** W2 (C1/D1): Optionen der Auslöser „Haltung“ und „Geste“ */
function PoseTriggerFields({ f, setF, zones, poseCams }: {
  f: RuleInput; setF: React.Dispatch<React.SetStateAction<RuleInput>>;
  zones: { id: string; name: string; color: string; cam: string }[]; poseCams: number;
}) {
  const o = f.trigger_options ?? {};
  const m = useMsgs().rules;
  const setO = (p: Partial<typeof o>) => setF((x) => ({ ...x, trigger_options: { ...x.trigger_options, ...p } }));
  const camHint = !poseCams && (
    <div className="hint" style={{ fontSize: 12, color: "var(--warn)" }}>
      {m.noPoseCam(f.camera_ids.length > 0, f.trigger === "gesture")}
    </div>
  );
  if (f.trigger === "gesture") {
    const gs = (o.gestures ?? []) as GestureName[];
    return (
      <div className="field"><label>{m.gesturesLabel}</label>
        <div className="gesture-pick">
          {ALL_GESTURES.map((g) => {
            const on = gs.includes(g);
            return (
              <button type="button" key={g} className={`gesture-opt ${on ? "on" : ""}`} title={GESTURE_HINT[g]}
                onClick={() => setO({ gestures: on ? gs.filter((x) => x !== g) : [...gs, g] })}>
                <GestureIcon g={g} size={30} /><span>{GESTURE_NAME[g]}</span>
              </button>
            );
          })}
        </div>
        <span className="hint muted" style={{ fontSize: 12 }}>{m.gestureHoldHint}</span>
        {camHint}
      </div>
    );
  }
  const excl = (o.exclude_zone_ids ?? []) as string[];
  return (
    <>
      <div className="form-grid">
        <div className="field"><label>{m.posture}</label>
          <div className="seg">
            {(["lying", "sitting", "standing"] as const).map((p) => (
              <button type="button" key={p} className={(o.posture ?? "lying") === p ? "on" : ""} onClick={() => setO({ posture: p })}>{POSTURE_TRIGGER_NAME[p]}</button>
            ))}
          </div>
        </div>
        <div className="field"><label>{m.onlyIfStill}</label>
          <Switch checked={o.max_motion != null} onChange={(v) => setO({ max_motion: v ? 0.05 : null })} label={o.max_motion != null ? m.littleMotion : m.any} />
          {o.max_motion != null && <Range value={Number(o.max_motion)} min={0.01} max={0.3} step={0.01} format={(v) => m.motionLe(fmtNum(v, 2))} onChange={(v) => setO({ max_motion: v })} />}
        </div>
      </div>
      <div className="field"><label>{m.excludeZones}</label>
        {zones.length ? (
          <ChipSelect options={zones.map((z) => z.id)} value={excl} onChange={(v) => setO({ exclude_zone_ids: v })}
            color={(id) => zones.find((z) => z.id === id)?.color}
            render={(id) => { const z = zones.find((x) => x.id === id)!; return <>{z.name} <span className="muted">· {z.cam}</span></>; }} />
        ) : <span className="muted" style={{ fontSize: 13 }}>{m.noZonesExclude}</span>}
      </div>
      {camHint}
    </>
  );
}

function RuleDialog({ rule, cams, idents, onClose }: { rule: Rule | null; cams: Camera[]; idents: Identity[]; onClose: () => void }) {
  const qc = useQueryClient();
  const msg = useMsgs();
  const m = msg.rules;
  const [f, setF] = useState<RuleInput>(() => {
    if (!rule) return EMPTY;
    const { id: _id, ...rest } = rule;
    void _id;
    return { ...EMPTY, ...rest, identity: rest.identity ?? EMPTY.identity };
  });
  const set = <K extends keyof RuleInput>(k: K, v: RuleInput[K]) => setF((p) => ({ ...p, [k]: v }));
  const availZones = useMemo(
    () => cams.filter((c) => !f.camera_ids.length || f.camera_ids.includes(c.id)).flatMap((c) => c.zones.map((z) => ({ ...z, cam: c.name }))),
    [cams, f.camera_ids],
  );
  const save = useMutation({
    mutationFn: () => {
      const zoneIds = new Set(availZones.map((z) => z.id));
      const keepDwell = f.trigger === "dwell" || f.trigger === "posture";
      const opts = f.trigger === "posture"
        ? { ...f.trigger_options, exclude_zone_ids: (f.trigger_options?.exclude_zone_ids ?? []).filter((z) => zoneIds.has(z)) }
        : f.trigger_options;
      const body: RuleInput = { ...f, name: f.name.trim(), zone_ids: f.zone_ids.filter((z) => zoneIds.has(z)), min_dwell_s: keepDwell ? f.min_dwell_s : 0,
        trigger_options: opts, repeat_s: isTrack ? f.repeat_s ?? 0 : 0, repeat_max: isTrack ? f.repeat_max ?? 0 : 0 };
      return rule ? api.updateRule(rule.id, body) : api.createRule(body);
    },
    onSuccess: () => { toast.success(m.toastSaved, f.name); qc.invalidateQueries({ queryKey: ["rules"] }); onClose(); },
    onError: (e: Error) => toast.error(m.toastSaveFailed, e.message),
  });
  const isTime = f.trigger === "time" || f.trigger === "sensor";   // ohne Track/Ereignis
  const isSensor = f.trigger === "sensor";
  const isTrack = !isTime;
  const sensors = useSensors();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const poseCams = cams.filter((c) => (f.trigger === "gesture" ? c.gesture_enabled : c.pose_enabled || c.gesture_enabled));
  const condText = useConditionSummary(f.conditions, cams);
  const valid = f.name.trim() && (isTime || f.identity.mode !== "specific" || f.identity.identity_ids.length > 0)
    && (f.trigger !== "time" || /^\d\d:\d\d$/.test(String(f.trigger_options?.at ?? "")))
    && (!isSensor || Number(f.trigger_options?.sensor_id ?? 0) > 0)
    && (f.trigger !== "gesture" || (f.trigger_options?.gestures ?? []).length > 0)
    && (!f.repeat_s || f.repeat_s >= 10)
    && f.conditions.every((c) => (c.type === "track_came_from" ? c.zone_ids.length > 0 : c.type === "sensor" ? c.sensor_id > 0
      : c.type === "weather" || c.labels.length > 0));
  const setTrigger = (t: RuleInput["trigger"]) => setF((p) => ({
    ...p, trigger: t,
    trigger_options: t === "time" ? { at: String(p.trigger_options?.at ?? "10:00") }
      : t === "posture" ? { posture: "lying", max_motion: null, exclude_zone_ids: [] }
      : t === "gesture" ? { gestures: ["arms_up"] }
      : t === "sensor" ? { sensor_id: Number(p.trigger_options?.sensor_id ?? 0), state: String(p.trigger_options?.state ?? "on") } : {},
    // Haltung/Geste gibt es nur für Personen; Liegedauer-Vorschlag aus den Einstellungen
    labels: t === "posture" || t === "gesture" ? ["person"] : p.labels,
    min_dwell_s: t === "posture" ? (p.trigger === "posture" ? p.min_dwell_s : settings.data?.fall_default_dwell_s ?? 60) : p.min_dwell_s,
    // „kam aus Zone“ braucht einen Track
    conditions: t === "time" || t === "sensor" ? p.conditions.filter((c) => c.type !== "track_came_from") : p.conditions,
    actions: t === "sensor" ? p.actions.filter((a) => a.type !== "clip") : p.actions,
  }));

  return (
    <Dialog size="lg" onClose={onClose} icon={<Workflow size={18} />} title={rule ? m.editRule : m.newRule} footer={<>
      <div className="spacer" style={{ fontSize: 13 }}>{ruleSummary(f, cams, idents, condText, sensors)}</div>
      <button className="btn btn-ghost" onClick={onClose}>{msg.common.cancel}</button>
      <button className="btn btn-primary" disabled={!valid || save.isPending} onClick={() => save.mutate()}>{save.isPending && <span className="spinner" />}{msg.common.save}</button>
    </>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
        <div className="form-grid">
          <div className="field"><label>{m.nameLabel}</label><input className="input" autoFocus value={f.name} onChange={(e) => set("name", e.target.value)} placeholder={m.namePlaceholder} /></div>
          <div className="field"><label>{m.status}</label><div style={{ height: 38, display: "flex", alignItems: "center" }}><Switch checked={f.enabled} onChange={(v) => set("enabled", v)} label={f.enabled ? msg.common.active : msg.common.inactive} /></div></div>
        </div>

        {!rule && (
          <div className="row-wrap" style={{ gap: 8, alignItems: "center" }}>
            <span className="muted row" style={{ gap: 6, fontSize: 13 }}><Wand2 size={14} />{m.template}</span>
            {ruleTemplates().map((t) => (
              <button type="button" key={t.id} className="btn btn-sm" title={t.text} onClick={() => setF((p) => t.build(p))}>{t.name}</button>
            ))}
          </div>
        )}

        {isSensor ? (
          <div className="muted" style={{ fontSize: 13 }}>
            {m.sensorRulesInfo}
          </div>
        ) : isTime ? (
          <div className="muted" style={{ fontSize: 13 }}>
            {m.timeRulesInfo}
          </div>
        ) : <>
        <fieldset className="fieldset"><legend>{m.legendWhere}</legend>
          <div className="field"><label>{m.camerasEmptyAll}</label>
            <ChipSelect options={cams.map((c) => c.id)} value={f.camera_ids} onChange={(v) => set("camera_ids", v)}
              render={(id) => <span className="row" style={{ gap: 6 }}><CamIcon size={13} />{cams.find((c) => c.id === id)?.name}</span>} />
          </div>
          <div className="field"><label>{m.zonesEmptyAll}</label>
            {availZones.length ? (
              <ChipSelect options={availZones.map((z) => z.id)} value={f.zone_ids} onChange={(v) => set("zone_ids", v)}
                color={(id) => availZones.find((z) => z.id === id)?.color}
                render={(id) => { const z = availZones.find((x) => x.id === id)!; return <>{z.name} <span className="muted">· {z.cam}</span></>; }} />
            ) : <span className="muted" style={{ fontSize: 13 }}>{m.noZonesForCams}</span>}
          </div>
        </fieldset>

        <fieldset className="fieldset"><legend>{m.legendWhat}</legend>
          <div className="field"><label>{m.objectsEmptyAll}</label>
            <ChipSelect options={[...ALL_LABELS]} value={f.labels} onChange={(v) => set("labels", v)} color={labelColor}
              render={(l) => <span className="row" style={{ gap: 6 }}><LabelIcon label={l} />{labelName(l)}</span>} />
          </div>
          <div className="form-grid">
            <div className="field"><label>{m.minConfidence}</label><Range value={f.min_score} min={0.1} max={0.99} step={0.01} format={pct} onChange={(v) => set("min_score", v)} /></div>
            <div className="field"><label>{m.identity}</label>
              <div className="seg">
                {(["any", "known", "unknown", "specific"] as const).map((md) => (
                  <button type="button" key={md} className={f.identity.mode === md ? "on" : ""} onClick={() => set("identity", { ...f.identity, mode: md })}>
                    {m.idMode[md]}
                  </button>
                ))}
              </div>
            </div>
          </div>
          {f.identity.mode === "specific" && (
            <div className="field"><label>{m.identities}</label>
              <SearchSelect multi inline value={f.identity.identity_ids} onChange={(v) => set("identity", { ...f.identity, identity_ids: v })}
                options={idents.map((i) => ({ value: i.id, label: i.display_name, image: i.cover_url, sub: KIND_NAME[i.kind] }))} placeholder={m.searchIdentity} />
            </div>
          )}
          {(f.identity.mode === "known" || f.identity.mode === "unknown") && <div className="hint muted" style={{ fontSize: 12 }}>{m.knownHint}</div>}
        </fieldset>
        </>}

        <fieldset className="fieldset"><legend>{m.legendWhen}</legend>
          <div className="form-grid">
            <div className="field span-2"><label>{m.triggerLabel}</label>
              <div className="seg">
                {(["enter", "leave", "dwell", "posture", "gesture", "time", "sensor"] as const).map((t) => (
                  <button type="button" key={t} className={f.trigger === t ? "on" : ""} onClick={() => setTrigger(t)}>
                    {TRIGGER_NAME[t]}
                  </button>
                ))}
              </div>
            </div>
            {f.trigger === "dwell" ? (
              <div className="field"><label>{m.minDwell}</label><Range value={f.min_dwell_s} min={1} max={600} step={1} format={(v) => `${v} s`} onChange={(v) => set("min_dwell_s", v)} /></div>
            ) : f.trigger === "posture" ? (
              <div className="field"><label>{m.postureMin}</label><Range value={f.min_dwell_s} min={5} max={900} step={5} format={fmtDuration} onChange={(v) => set("min_dwell_s", v)} /></div>
            ) : f.trigger === "time" ? (
              <div className="field"><label><Clock size={13} style={{ verticalAlign: -2 }} /> {m.timeLabel}</label>
                <input type="time" className="input" style={{ width: 130 }} value={String(f.trigger_options?.at ?? "")}
                  onChange={(e) => set("trigger_options", { at: e.target.value })} />
              </div>
            ) : <div />}
            <div className="field"><label>{m.cooldown(isTime)}</label><Range value={f.cooldown_s} min={0} max={3600} step={5} format={(v) => (v >= 60 ? `${Math.floor(v / 60)} min${v % 60 ? ` ${v % 60} s` : ""}` : `${v} s`)} onChange={(v) => set("cooldown_s", v)} /></div>
          </div>
          {(f.trigger === "posture" || f.trigger === "gesture") && (
            <PoseTriggerFields f={f} setF={setF} zones={availZones} poseCams={poseCams.length} />
          )}
          {isSensor && (
            <div className="field"><label>{m.sensorAndState}</label>
              <SensorStatePicker allowAny sensorId={Number(f.trigger_options?.sensor_id ?? 0)} state={String(f.trigger_options?.state ?? "on")}
                onChange={(id, st) => set("trigger_options", { sensor_id: id, state: st })} />
            </div>
          )}
          {isSensor ? null : f.trigger === "time" ? (
            <div className="field"><label>{m.onDays}</label>
              <ChipSelect options={[0, 1, 2, 3, 4, 5, 6]} value={f.schedule?.days ?? [0, 1, 2, 3, 4, 5, 6]}
                onChange={(d) => set("schedule", d.length === 7 ? null : { days: [...d].sort(), start: "00:00", end: "23:59" })} render={(d) => DAYS[d]} />
            </div>
          ) : <Switch checked={!!f.schedule} onChange={(v) => set("schedule", v ? { days: [0, 1, 2, 3, 4, 5, 6], start: "22:00", end: "06:00" } : null)} label={m.onlyAtTimes} />}
          {f.schedule && !isTime && (
            <div className="row-wrap" style={{ gap: 16 }}>
              <ChipSelect options={[0, 1, 2, 3, 4, 5, 6]} value={f.schedule.days} onChange={(d) => set("schedule", { ...f.schedule!, days: [...d].sort() })} render={(d) => DAYS[d]} />
              <div className="row">
                <input type="time" className="input input-sm" style={{ width: 110 }} value={f.schedule.start} onChange={(e) => set("schedule", { ...f.schedule!, start: e.target.value })} />
                <span className="muted">{m.until}</span>
                <input type="time" className="input input-sm" style={{ width: 110 }} value={f.schedule.end} onChange={(e) => set("schedule", { ...f.schedule!, end: e.target.value })} />
              </div>
              {f.schedule.start > f.schedule.end && <span className="muted" style={{ fontSize: 12 }}>{m.overMidnight}</span>}
            </div>
          )}
        </fieldset>

        <ConditionsBlock f={f} cams={cams} onChange={(p) => setF((x) => ({ ...x, ...p }))} />

        {isTrack && (
          <fieldset className="fieldset"><legend>{m.repeatLegend}</legend>
            <Switch checked={!!f.repeat_s} onChange={(v) => setF((p) => ({ ...p, repeat_s: v ? 120 : 0, repeat_max: v ? 3 : 0 }))}
              label={m.repeatSwitch} />
            {!!f.repeat_s && (
              <div className="form-grid">
                <div className="field"><label><Repeat size={13} style={{ verticalAlign: -2 }} /> {m.repeatEvery}</label>
                  <Range value={f.repeat_s} min={10} max={1800} step={10} format={fmtDuration} onChange={(v) => set("repeat_s", v)} /></div>
                <div className="field"><label>{m.atMost}</label>
                  <Range value={f.repeat_max ?? 0} min={0} max={20} step={1} format={(v) => (v ? `${v}×` : m.unlimited)} onChange={(v) => set("repeat_max", v)} /></div>
              </div>
            )}
            {!!f.repeat_s && <div className="hint muted" style={{ fontSize: 12 }}>
              {m.repeatHint1}<code>acknowledge_alert</code>{m.repeatHint2(f.trigger === "posture")}<code>{"{repeat}"}</code>{m.repeatHint3}
            </div>}
          </fieldset>
        )}

        <fieldset className="fieldset"><legend>{m.legendActions}</legend>
          {f.actions.map((a, i) => (
            <ActionEditor key={i} a={a} onChange={(n) => set("actions", f.actions.map((x, k) => (k === i ? n : x)))} onRemove={() => set("actions", f.actions.filter((_, k) => k !== i))} />
          ))}
          <div className="row-wrap">
            {(Object.keys(ACTION_META) as RuleAction["type"][]).filter((t) => (isSensor ? t !== "clip" : !isTime || (t !== "snapshot" && t !== "clip"))).map((t) => (
              <button type="button" key={t} className="btn btn-sm" onClick={() => set("actions", [...f.actions, newAction(t)])}><Plus size={14} />{ACTION_META[t].name}</button>
            ))}
          </div>
        </fieldset>
      </div>
    </Dialog>
  );
}

function RuleSummaryRow({ r, cams, idents }: { r: Rule; cams: Camera[]; idents: Identity[] }) {
  const condText = useConditionSummary(r.conditions ?? [], cams);
  const sensors = useSensors();
  const m = useMsgs().rules;
  return <>{ruleSummary(r, cams, idents, condText, sensors)}{r.conditions?.length ? <span className="badge outline" style={{ marginLeft: 6 }} title={m.withConditions}><ListChecks size={12} /></span> : null}</>;
}

export function RulesPage() {
  const qc = useQueryClient();
  const msg = useMsgs();
  const m = msg.rules;
  const rules = useQuery({ queryKey: ["rules"], queryFn: api.rules });
  const cams = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const idents = useQuery({ queryKey: ["identities", "all"], queryFn: () => api.identities({}) });
  const [edit, setEdit] = useState<Rule | null | "new">(null);
  const [del, setDel] = useState<Rule | null>(null);
  const toggle = useMutation({
    mutationFn: (r: Rule) => { const { id, ...rest } = r; return api.updateRule(id, { ...rest, enabled: !r.enabled }); },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rules"] }),
    onError: (e: Error) => toast.error(msg.common.error, e.message),
  });
  const dup = useMutation({
    mutationFn: (r: Rule) => { const { id: _id, ...rest } = r; void _id; return api.createRule({ ...rest, name: m.copyName(r.name), enabled: false }); },
    onSuccess: () => { toast.success(m.toastDuplicated); qc.invalidateQueries({ queryKey: ["rules"] }); },
  });
  const delM = useMutation({
    mutationFn: (id: number) => api.deleteRule(id),
    onSuccess: () => { toast.success(m.toastDeleted); qc.invalidateQueries({ queryKey: ["rules"] }); setDel(null); },
    onError: (e: Error) => toast.error(m.toastDeleteFailed, e.message),
  });

  return (
    <>
      <div className="page-header">
        <div><h1>{m.title}</h1><div className="sub">{m.sub}</div></div>
        <button className="btn btn-primary" onClick={() => setEdit("new")}><Plus size={16} />{m.newRule}</button>
      </div>
      {rules.isLoading ? <Loading /> : !rules.data?.length ? (
        <div className="card"><Empty icon={<Workflow size={44} />} title={m.emptyTitle} text={m.emptyText} /></div>
      ) : (
        <div className="list">
          {rules.data.map((r, i) => (
            <div key={r.id} className={`list-item ${r.enabled ? "" : "disabled"}`} style={{ animationDelay: `${i * 30}ms` }}>
              <Switch checked={r.enabled} onChange={() => toggle.mutate(r)} />
              <div className="grow">
                <div className="title">{r.name}{!r.enabled && <span className="badge">{msg.common.inactive}</span>}</div>
                <div style={{ marginTop: 4 }}><RuleSummaryRow r={r} cams={cams.data ?? []} idents={idents.data ?? []} /></div>
              </div>
              <div className="row" style={{ gap: 4 }}>{r.actions.map((a, k) => <span key={k} className="badge accent" title={ACTION_META[a.type].name}>{ACTION_META[a.type].icon}</span>)}</div>
              <button className="btn btn-sm" onClick={() => setEdit(r)}><Pencil size={14} />{msg.common.edit}</button>
              <button className="btn btn-sm btn-ghost btn-icon" title={m.duplicate} onClick={() => dup.mutate(r)}><Copy size={15} /></button>
              <button className="btn btn-sm btn-ghost btn-icon" title={msg.common.delete} onClick={() => setDel(r)}><Trash2 size={15} /></button>
            </div>
          ))}
        </div>
      )}
      {edit && <RuleDialog rule={edit === "new" ? null : edit} cams={cams.data ?? []} idents={idents.data ?? []} onClose={() => setEdit(null)} />}
      {del && <ConfirmDialog title={m.deleteTitle} busy={delM.isPending} text={m.deleteText(del.name)} onConfirm={() => delM.mutate(del.id)} onClose={() => setDel(null)} />}
    </>
  );
}
