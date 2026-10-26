import { groupName } from "../lib/labels";
import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleHelp, CloudRain, Footprints, Gauge, Hand, Package, Play, Plus, X, XCircle } from "lucide-react";
import { api } from "../lib/api";
import type { Camera, InventoryVocabEntry, RuleCheckResult, RuleCondition, RuleInput, SensorState, VirtualSensor } from "../lib/types";
import { Range } from "./ui";
import { lang, msgs, useMsgs } from "../i18n";

/**
 * A1: Block „Nur wenn …“ im Regel-Editor (Bedingungen, Verzögerung, „Jetzt prüfen“) und Vorlagen.
 * Bedingungstypen laut CONTRACT.md W1.B; alle müssen erfüllt sein.
 */

type CondType = RuleCondition["type"];

const COND_BASE: Record<CondType, { icon: React.ReactNode; trackOnly?: boolean }> = {
  inventory: { icon: <Package size={15} /> },
  inventory_untouched: { icon: <Hand size={15} /> },
  weather: { icon: <CloudRain size={15} /> },
  track_came_from: { icon: <Footprints size={15} />, trackOnly: true },
  sensor: { icon: <Gauge size={15} /> },   // W3 (A3)
};

/** Bedingungstypen mit Namen in der aktuellen Sprache */
export function condMeta(): Record<CondType, { name: string; icon: React.ReactNode; trackOnly?: boolean }> {
  const names = msgs().rules.cond;
  return Object.fromEntries((Object.keys(COND_BASE) as CondType[]).map((k) => [k, { ...COND_BASE[k], name: names[k] }])) as
    Record<CondType, { name: string; icon: React.ReactNode; trackOnly?: boolean }>;
}

/** W3 (A3): Zustände virtueller Sensoren (wie {sensor_state}), Text aus der aktuellen Sprache */
export const SENSOR_STATE_NAME = new Proxy({} as Record<SensorState, string>, {
  get: (_t, k) => (typeof k === "string" ? msgs().rules.sensorState[k] : undefined),
});

export function useSensors(): VirtualSensor[] {
  const q = useQuery({ queryKey: ["sensors"], queryFn: () => api.sensors() });
  return q.data ?? [];
}

export function newCondition(t: CondType): RuleCondition {
  switch (t) {
    case "inventory": return { type: "inventory", labels: ["keys"], state: "present", camera_ids: [], room: null, zone_ids: [] };
    case "inventory_untouched": return { type: "inventory_untouched", labels: ["medicine"], since: "06:00", camera_ids: [], zone_ids: [] };
    case "weather": return { type: "weather", metric: "rain_probability", min: 60, hours: 12 };
    case "track_came_from": return { type: "track_came_from", zone_ids: [] };
    case "sensor": return { type: "sensor", sensor_id: 0, state: "on" };
  }
}

function useVocab(): InventoryVocabEntry[] {
  const st = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  return st.data?.inventory_vocabulary ?? [];
}

/** Anzeigename eines Vokabular-Eintrags in der aktuellen Sprache (CONTRACT I18N.C) */
function entryName(v: InventoryVocabEntry): string {
  const l = lang();
  return v.names?.[l] ?? (l === "de" ? v.name_de : v.names?.en ?? v.prompts[0] ?? v.label);
}

function vocabName(vocab: InventoryVocabEntry[], label: string) {
  const v = vocab.find((x) => x.label === label);
  return v ? entryName(v) : label;
}

function joinUnd(xs: string[]) {
  if (xs.length <= 1) return xs.join("");
  return `${xs.slice(0, -1).join(", ")} ${msgs().rules.and} ${xs[xs.length - 1]}`;
}

/** Kurzer Text je Bedingung (Regel-Übersicht) */
export function conditionText(c: RuleCondition, vocab: InventoryVocabEntry[], cams: Camera[], sensors: VirtualSensor[] = []): string {
  const zoneName = (id: string) => cams.flatMap((x) => x.zones).find((z) => z.id === id)?.name ?? id;
  const m = msgs().rules;
  switch (c.type) {
    case "inventory": {
      const where = c.zone_ids.length ? m.condIn(joinUnd(c.zone_ids.map(zoneName))) : c.room ? m.condIn(c.room) : "";
      return m.condInventory(joinUnd(c.labels.map((l) => vocabName(vocab, l))), c.state === "present", where);
    }
    case "inventory_untouched": return m.condUntouched(joinUnd(c.labels.map((l) => vocabName(vocab, l))), c.since);
    case "weather": return m.condWeather(c.min, c.hours);
    case "track_came_from": return m.condCameFrom(joinUnd(c.zone_ids.map(zoneName)) || "?");
    case "sensor": return m.condSensor(sensors.find((x) => x.id === c.sensor_id)?.name ?? (c.sensor_id ? `#${c.sensor_id}` : "?"), SENSOR_STATE_NAME[c.state] ?? c.state);
  }
}

export function useConditionSummary(conds: RuleCondition[], cams: Camera[]): string {
  const vocab = useVocab();
  const sensors = useSensors();
  const m = useMsgs();
  return conds.map((c) => conditionText(c, vocab, cams, sensors)).join(` ${m.rules.and} `);
}

/** W3 (A3): Sensor + Zustand wählen (Bedingung „Sensor ist …“ und Auslöser „Sensor“) */
export function SensorStatePicker({ sensorId, state, onChange, allowAny, allowUnknown }: {
  sensorId: number; state: string; onChange: (sensorId: number, state: string) => void; allowAny?: boolean; allowUnknown?: boolean;
}) {
  const sensors = useSensors();
  const m = useMsgs().rules;
  const s = sensors.find((x) => x.id === sensorId);
  const states: string[] = [...(s?.state_set === "level" ? ["ok", "low", "out"] : ["on", "off"]),
    ...(allowUnknown ? ["unknown"] : []), ...(allowAny ? ["any"] : [])];
  if (!sensors.length) return <span className="muted" style={{ fontSize: 13 }}>{m.noSensors}</span>;
  return (
    <div className="row-wrap" style={{ gap: 8 }}>
      <select className="select" style={{ width: "auto", minWidth: 200 }} value={sensorId || ""}
        onChange={(e) => {
          const n = sensors.find((x) => x.id === Number(e.target.value));
          const first = n?.state_set === "level" ? "low" : "on";
          onChange(Number(e.target.value), states.includes(state) && (n?.state_set ?? "binary") === (s?.state_set ?? "binary") ? state : first);
        }}>
        <option value="">{m.chooseSensor}</option>
        {sensors.map((x) => <option key={x.id} value={x.id}>{x.name} · {x.camera_name}{x.supply ? m.supplySuffix : ""}</option>)}
      </select>
      <div className="seg">
        {states.map((st) => (
          <button type="button" key={st} className={state === st ? "on" : ""} onClick={() => onChange(sensorId, st)}>
            {st === "any" ? m.anyChange : SENSOR_STATE_NAME[st as SensorState]}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Mehrfachauswahl aus dem Gegenstands-Vokabular (gruppiert) */
function LabelPicker({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const vocab = useVocab();
  const m = useMsgs();
  const groups = useMemo(() => {
    const g = new Map<string, InventoryVocabEntry[]>();
    for (const v of vocab) {
      if (value.includes(v.label)) continue;
      const k = v.group ? groupName(v.group) : m.rules.otherGroup;
      g.set(k, [...(g.get(k) ?? []), v]);
    }
    return [...g.entries()];
  }, [vocab, value, m]);
  return (
    <div className="row-wrap" style={{ gap: 6 }}>
      {value.map((l) => (
        <span key={l} className="chip on">{vocabName(vocab, l)}
          {value.length > 1 && <button type="button" className="btn btn-ghost btn-icon btn-sm" style={{ width: 18, height: 18, padding: 0 }} title={m.common.remove}
            onClick={() => onChange(value.filter((x) => x !== l))}><X size={12} /></button>}
        </span>
      ))}
      <select className="select input-sm" style={{ width: "auto", minWidth: 150 }} value=""
        onChange={(e) => e.target.value && onChange([...value, e.target.value])}>
        <option value="">{m.rules.addItem}</option>
        {groups.map(([g, items]) => (
          <optgroup key={g} label={g}>{items.map((v) => <option key={v.label} value={v.label}>{entryName(v)}</option>)}</optgroup>
        ))}
      </select>
    </div>
  );
}

function ZonePicker({ cams, cameraIds, value, onChange, empty }: { cams: Camera[]; cameraIds: number[]; value: string[]; onChange: (v: string[]) => void; empty: string }) {
  const m = useMsgs().rules;
  const zones = cams.filter((c) => !cameraIds.length || cameraIds.includes(c.id)).flatMap((c) => c.zones.map((z) => ({ ...z, cam: c.name })));
  if (!zones.length) return <span className="muted" style={{ fontSize: 13 }}>{m.noZones}</span>;
  return (
    <div className="chips">
      {zones.map((z) => {
        const on = value.includes(z.id);
        return (
          <button type="button" key={`${z.cam}-${z.id}`} className={`chip ${on ? "on" : ""}`} style={{ ["--chip-c" as string]: z.color }}
            onClick={() => onChange(on ? value.filter((v) => v !== z.id) : [...value, z.id])}>
            <span className="dot" />{z.name} <span className="muted">· {z.cam}</span>
          </button>
        );
      })}
      {!value.length && <span className="muted" style={{ fontSize: 12, alignSelf: "center" }}>{empty}</span>}
    </div>
  );
}

function CameraPicker({ cams, value, onChange }: { cams: Camera[]; value: number[]; onChange: (v: number[]) => void }) {
  const m = useMsgs().rules;
  return (
    <div className="chips">
      {cams.map((c) => {
        const on = value.includes(c.id);
        return (
          <button type="button" key={c.id} className={`chip ${on ? "on" : ""}`} onClick={() => onChange(on ? value.filter((v) => v !== c.id) : [...value, c.id])}>
            {c.name}{c.inventory_enabled ? "" : <span className="muted">{m.noInventorySuffix}</span>}
          </button>
        );
      })}
    </div>
  );
}

function ConditionEditor({ c, cams, onChange, onRemove }: { c: RuleCondition; cams: Camera[]; onChange: (c: RuleCondition) => void; onRemove: () => void }) {
  const m = useMsgs().rules;
  const meta = condMeta()[c.type];
  const rooms = [...new Set(cams.map((x) => x.room).filter((r): r is string => !!r))];
  return (
    <div className="action-card">
      <div className="ah"><span className="ico">{meta.icon}</span>{meta.name}<span className="spacer" />
        <button type="button" className="btn btn-ghost btn-icon btn-sm" onClick={onRemove} title={m.removeCondition}><X size={15} /></button>
      </div>
      {c.type === "inventory" && (
        <div className="form-grid">
          <div className="field span-2"><label>{m.item}</label><LabelPicker value={c.labels} onChange={(v) => onChange({ ...c, labels: v })} /></div>
          <div className="field"><label>{m.is}</label>
            <div className="seg">
              <button type="button" className={c.state === "present" ? "on" : ""} onClick={() => onChange({ ...c, state: "present" })}>{m.stillThere}</button>
              <button type="button" className={c.state === "absent" ? "on" : ""} onClick={() => onChange({ ...c, state: "absent" })}>{m.gone}</button>
            </div>
          </div>
          <div className="field"><label>{m.inRoom}</label>
            <select className="select" value={c.room ?? ""} onChange={(e) => onChange({ ...c, room: e.target.value || null })}>
              <option value="">{m.any}</option>
              {rooms.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
          <div className="field span-2"><label>{m.camerasEmptyAll}</label><CameraPicker cams={cams} value={c.camera_ids} onChange={(v) => onChange({ ...c, camera_ids: v })} /></div>
          <div className="field span-2"><label>{m.inZoneItem}</label>
            <ZonePicker cams={cams} cameraIds={c.camera_ids} value={c.zone_ids} onChange={(v) => onChange({ ...c, zone_ids: v })} empty={m.emptyEverywhere} />
          </div>
          <div className="hint muted span-2" style={{ fontSize: 12 }}>
            {m.hintInventory}
          </div>
        </div>
      )}
      {c.type === "inventory_untouched" && (
        <div className="form-grid">
          <div className="field span-2"><label>{m.item}</label><LabelPicker value={c.labels} onChange={(v) => onChange({ ...c, labels: v })} /></div>
          <div className="field"><label>{m.untouchedSince}</label>
            <input type="time" className="input" value={c.since} onChange={(e) => e.target.value && onChange({ ...c, since: e.target.value })} />
          </div>
          <div className="field"><label>{m.camerasEmptyAll}</label><CameraPicker cams={cams} value={c.camera_ids} onChange={(v) => onChange({ ...c, camera_ids: v })} /></div>
          <div className="field span-2"><label>{m.nobodyInZone}</label>
            <ZonePicker cams={cams} cameraIds={c.camera_ids} value={c.zone_ids} onChange={(v) => onChange({ ...c, zone_ids: v })} empty={m.emptyNoPersonCheck} />
          </div>
          <div className="hint muted span-2" style={{ fontSize: 12 }}>
            {m.hintUntouched}
          </div>
        </div>
      )}
      {c.type === "weather" && (
        <div className="form-grid">
          <div className="field"><label>{m.rainAtLeast}</label>
            <Range value={c.min} min={0} max={100} step={5} format={(v) => `${v} %`} onChange={(v) => onChange({ ...c, min: v })} /></div>
          <div className="field"><label>{m.withinNext}</label>
            <Range value={c.hours} min={1} max={48} step={1} format={(v) => `${v} h`} onChange={(v) => onChange({ ...c, hours: v })} /></div>
          <div className="hint muted span-2" style={{ fontSize: 12 }}>
            {m.hintWeather}
          </div>
        </div>
      )}
      {c.type === "sensor" && (
        <div className="field"><label>{m.sensorIsNow}</label>
          <SensorStatePicker sensorId={c.sensor_id} state={c.state} allowUnknown
            onChange={(id, st) => onChange({ ...c, sensor_id: id, state: st as SensorState })} />
        </div>
      )}
      {c.type === "track_came_from" && (
        <div className="form-grid">
          <div className="field span-2"><label>{m.cameFromLabel}</label>
            <ZonePicker cams={cams} cameraIds={[]} value={c.zone_ids} onChange={(v) => onChange({ ...c, zone_ids: v })} empty={m.pickZone} />
          </div>
          <div className="hint muted span-2" style={{ fontSize: 12 }}>
            {m.hintCameFrom}
          </div>
        </div>
      )}
    </div>
  );
}

function CheckResult({ r }: { r: RuleCheckResult }) {
  const m = useMsgs().rules;
  const meta = condMeta();
  return (
    <div className="card" style={{ background: "var(--bg-3)" }}>
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 13 }}>
        <b style={{ color: r.ok ? "var(--ok)" : "var(--bad)" }}>{r.ok ? m.checkOk : m.checkNotOk}</b>
        {r.details.map((d, i) => (
          <div key={i} className="row" style={{ gap: 6, alignItems: "flex-start" }}>
            {d.ok === null ? <CircleHelp size={15} className="muted" /> : d.ok ? <CheckCircle2 size={15} style={{ color: "var(--ok)" }} /> : <XCircle size={15} style={{ color: "var(--bad)" }} />}
            <span><b>{meta[d.type]?.name ?? d.type}:</b> {d.reason}</span>
          </div>
        ))}
        {r.preview && <div className="muted">{m.previewPush} <b>{r.preview.title}</b> – {r.preview.message}</div>}
      </div>
    </div>
  );
}

export function ConditionsBlock({ f, cams, onChange }: { f: RuleInput; cams: Camera[]; onChange: (p: Partial<RuleInput>) => void }) {
  const isTime = f.trigger === "time" || f.trigger === "sensor";   // ohne Track (W3: auch Sensor)
  const isSensor = f.trigger === "sensor";
  const [result, setResult] = useState<RuleCheckResult | null>(null);
  const m = useMsgs().rules;
  const meta = condMeta();
  const check = useMutation({
    mutationFn: () => api.checkRule({ ...f, name: f.name.trim() || m.draft }),
    onSuccess: setResult,
    onError: (e: Error) => setResult({ ok: false, reason: e.message, details: [], placeholders: { items: "", item_place: "", weather: "" }, item_ids: [], preview: null }),
  });
  const set = (conds: RuleCondition[]) => { setResult(null); onChange({ conditions: conds }); };
  const types = (Object.keys(meta) as CondType[]).filter((t) => !(isTime && meta[t].trackOnly));
  return (
    <fieldset className="fieldset"><legend>{m.onlyIf}</legend>
      {!f.conditions.length && (
        <div className="muted" style={{ fontSize: 13 }}>
          {m.noConditions(isSensor ? "sensor" : isTime ? "time" : "track")}
        </div>
      )}
      {f.conditions.map((c, i) => (
        <ConditionEditor key={i} c={c} cams={cams}
          onChange={(n) => set(f.conditions.map((x, k) => (k === i ? n : x)))}
          onRemove={() => set(f.conditions.filter((_, k) => k !== i))} />
      ))}
      <div className="row-wrap">
        {types.map((t) => (
          <button type="button" key={t} className="btn btn-sm" onClick={() => set([...f.conditions, newCondition(t)])}><Plus size={14} />{meta[t].name}</button>
        ))}
        {f.conditions.length > 0 && (
          <button type="button" className="btn btn-sm btn-ghost" disabled={check.isPending} onClick={() => check.mutate()}>
            {check.isPending ? <span className="spinner" /> : <Play size={14} />}{m.checkNow}
          </button>
        )}
      </div>
      {f.conditions.length > 0 && !isSensor && (
        <div className="field"><label>{isTime ? m.checkDelayTime : m.checkDelayTrigger}</label>
          <Range value={f.condition_delay_s} min={0} max={600} step={5}
            format={(v) => (v >= 60 ? `${Math.floor(v / 60)} min${v % 60 ? ` ${v % 60} s` : ""}` : `${v} s`)}
            onChange={(v) => onChange({ condition_delay_s: v })} />
          {!isTime && f.conditions.some((c) => c.type === "inventory") && f.condition_delay_s < 120 && (
            <span className="hint" style={{ color: "var(--warn)" }}>
              {m.delayRecommend}
            </span>
          )}
        </div>
      )}
      {result && <CheckResult r={result} />}
    </fieldset>
  );
}

/* ---------- Vorlagen ---------- */

export interface RuleTemplate { id: string; name: string; text: string; build: (base: RuleInput) => RuleInput }

const pushAction = (message: string) => ({ type: "push" as const, target: null, title: "{rule}", message, priority: "default" as const, image: "crop" as const });

/** Vorlagen in der aktuellen Sprache (Namen und Push-Texte) */
export function ruleTemplates(): RuleTemplate[] {
  const m = msgs().rules;
  return [
    {
      id: "keys", name: m.tplKeys.name, text: m.tplKeys.text,
      build: (b) => ({ ...b, name: m.tplKeys.ruleName, labels: ["person"], trigger: "enter", trigger_options: {}, cooldown_s: 600,
        conditions: [newCondition("inventory")], condition_delay_s: 120,
        actions: [pushAction(m.tplKeys.message)] }),
    },
    {
      id: "umbrella", name: m.tplUmbrella.name, text: m.tplUmbrella.text,
      build: (b) => ({ ...b, name: m.tplUmbrella.ruleName, labels: ["person"], trigger: "enter", trigger_options: {}, cooldown_s: 1800,
        conditions: [{ ...newCondition("inventory"), labels: ["umbrella"] } as RuleCondition, newCondition("weather")], condition_delay_s: 120,
        actions: [pushAction(m.tplUmbrella.message)] }),
    },
    {
      id: "medicine", name: m.tplMedicine.name, text: m.tplMedicine.text,
      build: (b) => ({ ...b, name: m.tplMedicine.ruleName, trigger: "time", trigger_options: { at: "10:00" }, cooldown_s: 0, schedule: null,
        camera_ids: [], zone_ids: [], labels: [], identity: { mode: "any", identity_ids: [] },
        conditions: [newCondition("inventory_untouched")], condition_delay_s: 0,
        actions: [pushAction(m.tplMedicine.message)] }),
    },
  ];
}
