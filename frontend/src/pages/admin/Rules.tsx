import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, Camera as CamIcon, Clapperboard, Copy, Globe, Image as ImageIcon, Pencil, Plus, Radio, Trash2, Workflow, X } from "lucide-react";
import { api } from "../../lib/api";
import type { Camera, Identity, Rule, RuleAction, RuleInput } from "../../lib/types";
import { ALL_LABELS, DAYS, KIND_NAME, labelColor, labelName } from "../../lib/labels";
import { ChipSelect, ConfirmDialog, Dialog, Empty, LabelIcon, Loading, Range, SearchSelect, Switch } from "../../components/ui";
import { toast } from "../../lib/store";

const PLACEHOLDERS = ["{camera}", "{camera_id}", "{label}", "{score}", "{identity}", "{zone}", "{event_id}", "{rule}", "{time}", "{thumb_url}", "{snapshot_url}", "{clip_url}"];

const EMPTY: RuleInput = {
  name: "", enabled: true, camera_ids: [], zone_ids: [], labels: ["person"], min_score: 0.6,
  identity: { mode: "any", identity_ids: [] }, trigger: "enter", trigger_options: {}, min_dwell_s: 0, schedule: null, cooldown_s: 30,
  conditions: [], condition_delay_s: 0,
  actions: [{ type: "snapshot" }],
};

const ACTION_META: Record<RuleAction["type"], { name: string; icon: React.ReactNode }> = {
  snapshot: { name: "Snapshot", icon: <ImageIcon size={15} /> },
  clip: { name: "Clip", icon: <Clapperboard size={15} /> },
  mqtt: { name: "MQTT", icon: <Radio size={15} /> },
  webhook: { name: "Webhook", icon: <Globe size={15} /> },
  push: { name: "Push", icon: <BellRing size={15} /> },
};

function newAction(t: RuleAction["type"]): RuleAction {
  switch (t) {
    case "snapshot": return { type: "snapshot" };
    case "clip": return { type: "clip", pre_s: 5, post_s: 10 };
    case "mqtt": return { type: "mqtt", topic: "visionguard/alarm/{camera}", payload: null, retain: false };
    case "webhook": return { type: "webhook", url: "", method: "POST", headers: {}, body: null };
    case "push": return { type: "push", target: null, title: "{rule}", message: "{label} an {camera} ({zone})", priority: "default", image: "snapshot" };
  }
}

function joinOr(items: string[]) {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} oder ${items[items.length - 1]}`;
}

export function ruleSummary(r: RuleInput, cams: Camera[], idents: Identity[]): React.ReactNode {
  const who = r.labels.length ? joinOr(r.labels.map(labelName)) : "ein Objekt";
  const idTxt = r.identity.mode === "known" ? " (bekannt)" : r.identity.mode === "unknown" ? " (unbekannt)"
    : r.identity.mode === "specific" ? ` (${joinOr(r.identity.identity_ids.map((id) => idents.find((i) => i.id === id)?.display_name ?? `#${id}`)) || "?"})` : "";
  const zones = cams.flatMap((c) => c.zones).filter((z) => r.zone_ids.includes(z.id)).map((z) => z.name);
  const camNames = r.camera_ids.map((id) => cams.find((c) => c.id === id)?.name ?? `#${id}`);
  const where = zones.length ? <>in Zone <b>{joinOr(zones)}</b></> : camNames.length ? <>an <b>{joinOr(camNames)}</b></> : <>an <b>allen Kameras</b></>;
  const verb = r.trigger === "enter" ? "erscheint" : r.trigger === "leave" ? "verschwindet"
    : r.trigger === "time" ? `– täglich um ${r.trigger_options?.at ?? "?"}` : `länger als ${r.min_dwell_s} s verweilt`;
  const sched = r.schedule ? ` · ${r.schedule.days.length === 7 ? "täglich" : r.schedule.days.map((d) => DAYS[d]).join(", ")} ${r.schedule.start}–${r.schedule.end}` : "";
  const acts = r.actions.map((a) => ACTION_META[a.type].name);
  return (
    <span className="rule-summary">
      Wenn <b>{who}{idTxt}</b> {where} {verb}
      <span className="arrow">→</span>{acts.length ? acts.join(", ") : <span className="muted">keine Aktion</span>}
      {sched && <span className="muted">{sched}</span>}
    </span>
  );
}

function TemplateInput({ value, onChange, placeholder, multiline }: { value: string; onChange: (v: string) => void; placeholder?: string; multiline?: boolean }) {
  const ref = useRef<HTMLInputElement & HTMLTextAreaElement>(null);
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
      <div className="placeholders">{PLACEHOLDERS.map((p) => <code key={p} title="Einfügen" onClick={() => insert(p)}>{p}</code>)}</div>
    </div>
  );
}

function HeadersInput({ value, onChange }: { value: Record<string, string>; onChange: (v: Record<string, string>) => void }) {
  const [text, setText] = useState(Object.keys(value).length ? JSON.stringify(value, null, 2) : "");
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="field"><label>Header (JSON)</label>
      <textarea className="textarea" style={{ minHeight: 64 }} value={text} placeholder={'{"Authorization": "Bearer …"}'}
        onChange={(e) => {
          setText(e.target.value);
          if (!e.target.value.trim()) { setErr(null); onChange({}); return; }
          try {
            const j = JSON.parse(e.target.value);
            if (typeof j !== "object" || Array.isArray(j) || j === null) throw new Error("Objekt erwartet");
            setErr(null); onChange(Object.fromEntries(Object.entries(j).map(([k, v]) => [k, String(v)])));
          } catch (x) { setErr((x as Error).message); }
        }} />
      {err && <span className="hint" style={{ color: "var(--bad)" }}>Ungültiges JSON: {err}</span>}
    </div>
  );
}

/** W1 (K1): Aktion „Push“ – Ziele aus Einstellungen → Benachrichtigungen */
function PushActionFields({ a, onChange }: { a: Extract<RuleAction, { type: "push" }>; onChange: (a: RuleAction) => void }) {
  const st = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const targets = st.data?.push_targets ?? [];
  return (
    <div className="form-grid">
      <div className="field"><label>Ziel</label>
        <select className="select" value={a.target ?? ""} onChange={(e) => onChange({ ...a, target: e.target.value || null })}>
          <option value="">Alle aktiven Ziele</option>
          {targets.map((t) => <option key={t.id} value={t.id}>{t.name}{t.enabled ? "" : " (deaktiviert)"}</option>)}
        </select>
        {!targets.length && <span className="hint">Noch keine Push-Ziele – unter Einstellungen → Benachrichtigungen anlegen.</span>}
      </div>
      <div className="field"><label>Priorität / Bild</label>
        <div className="row">
          <select className="select" value={a.priority} onChange={(e) => onChange({ ...a, priority: e.target.value as typeof a.priority })}>
            <option value="low">niedrig</option><option value="default">normal</option><option value="high">hoch</option>
          </select>
          <select className="select" value={a.image} onChange={(e) => onChange({ ...a, image: e.target.value as typeof a.image })}>
            <option value="snapshot">Snapshot</option><option value="crop">Ausschnitt</option><option value="none">kein Bild</option>
          </select>
        </div>
      </div>
      <div className="field span-2"><label>Titel</label><TemplateInput value={a.title} onChange={(v) => onChange({ ...a, title: v })} /></div>
      <div className="field span-2"><label>Text</label><TemplateInput multiline value={a.message} onChange={(v) => onChange({ ...a, message: v })} /></div>
    </div>
  );
}

function ActionEditor({ a, onChange, onRemove }: { a: RuleAction; onChange: (a: RuleAction) => void; onRemove: () => void }) {
  const meta = ACTION_META[a.type];
  return (
    <div className="action-card">
      <div className="ah"><span className="ico">{meta.icon}</span>{meta.name}<span className="spacer" />
        <button type="button" className="btn btn-ghost btn-icon btn-sm" onClick={onRemove} title="Aktion entfernen"><X size={15} /></button>
      </div>
      {a.type === "snapshot" && <div className="muted" style={{ fontSize: 13 }}>Speichert ein Vollbild zum Ereignis.</div>}
      {a.type === "push" && <PushActionFields a={a} onChange={onChange} />}
      {a.type === "clip" && (
        <div className="form-grid">
          <div className="field"><label>Vorlauf</label><Range value={a.pre_s} min={0} max={30} step={1} format={(v) => `${v} s`} onChange={(v) => onChange({ ...a, pre_s: v })} /></div>
          <div className="field"><label>Nachlauf</label><Range value={a.post_s} min={1} max={60} step={1} format={(v) => `${v} s`} onChange={(v) => onChange({ ...a, post_s: v })} /></div>
        </div>
      )}
      {a.type === "mqtt" && (
        <div className="form-grid">
          <div className="field span-2"><label>Topic</label><TemplateInput value={a.topic} onChange={(v) => onChange({ ...a, topic: v })} /></div>
          <div className="field span-2"><label>Payload (leer = Event-JSON)</label><TemplateInput multiline value={a.payload ?? ""} placeholder="leer = vollständiges Event als JSON" onChange={(v) => onChange({ ...a, payload: v || null })} /></div>
          <Switch checked={a.retain} onChange={(v) => onChange({ ...a, retain: v })} label="Retain" />
        </div>
      )}
      {a.type === "webhook" && (
        <div className="form-grid">
          <div className="field span-2"><label>URL</label><TemplateInput value={a.url} placeholder="https://example.org/hook?cam={camera}" onChange={(v) => onChange({ ...a, url: v })} /></div>
          <div className="field"><label>Methode</label>
            <select className="select" value={a.method} onChange={(e) => onChange({ ...a, method: e.target.value })}>
              {["POST", "PUT", "PATCH", "GET"].map((m) => <option key={m}>{m}</option>)}
            </select>
          </div>
          <div />
          <div className="span-2"><HeadersInput value={a.headers} onChange={(h) => onChange({ ...a, headers: h })} /></div>
          <div className="field span-2"><label>Body (leer = Event-JSON)</label><TemplateInput multiline value={a.body ?? ""} placeholder="leer = vollständiges Event als JSON" onChange={(v) => onChange({ ...a, body: v || null })} /></div>
        </div>
      )}
    </div>
  );
}

function RuleDialog({ rule, cams, idents, onClose }: { rule: Rule | null; cams: Camera[]; idents: Identity[]; onClose: () => void }) {
  const qc = useQueryClient();
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
      const body: RuleInput = { ...f, name: f.name.trim(), zone_ids: f.zone_ids.filter((z) => zoneIds.has(z)), min_dwell_s: f.trigger === "dwell" ? f.min_dwell_s : 0 };
      return rule ? api.updateRule(rule.id, body) : api.createRule(body);
    },
    onSuccess: () => { toast.success("Regel gespeichert", f.name); qc.invalidateQueries({ queryKey: ["rules"] }); onClose(); },
    onError: (e: Error) => toast.error("Speichern fehlgeschlagen", e.message),
  });
  const valid = f.name.trim() && (f.identity.mode !== "specific" || f.identity.identity_ids.length > 0);

  return (
    <Dialog size="lg" onClose={onClose} icon={<Workflow size={18} />} title={rule ? "Regel bearbeiten" : "Neue Regel"} footer={<>
      <div className="spacer" style={{ fontSize: 13 }}>{ruleSummary(f, cams, idents)}</div>
      <button className="btn btn-ghost" onClick={onClose}>Abbrechen</button>
      <button className="btn btn-primary" disabled={!valid || save.isPending} onClick={() => save.mutate()}>{save.isPending && <span className="spinner" />}Speichern</button>
    </>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
        <div className="form-grid">
          <div className="field"><label>Name *</label><input className="input" autoFocus value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="z. B. Person an Einfahrt nachts" /></div>
          <div className="field"><label>Status</label><div style={{ height: 38, display: "flex", alignItems: "center" }}><Switch checked={f.enabled} onChange={(v) => set("enabled", v)} label={f.enabled ? "aktiv" : "inaktiv"} /></div></div>
        </div>

        <fieldset className="fieldset"><legend>Wo</legend>
          <div className="field"><label>Kameras (leer = alle)</label>
            <ChipSelect options={cams.map((c) => c.id)} value={f.camera_ids} onChange={(v) => set("camera_ids", v)}
              render={(id) => <span className="row" style={{ gap: 6 }}><CamIcon size={13} />{cams.find((c) => c.id === id)?.name}</span>} />
          </div>
          <div className="field"><label>Zonen (leer = ganzes Bild)</label>
            {availZones.length ? (
              <ChipSelect options={availZones.map((z) => z.id)} value={f.zone_ids} onChange={(v) => set("zone_ids", v)}
                color={(id) => availZones.find((z) => z.id === id)?.color}
                render={(id) => { const z = availZones.find((x) => x.id === id)!; return <>{z.name} <span className="muted">· {z.cam}</span></>; }} />
            ) : <span className="muted" style={{ fontSize: 13 }}>Für die gewählten Kameras sind keine Zonen definiert.</span>}
          </div>
        </fieldset>

        <fieldset className="fieldset"><legend>Was</legend>
          <div className="field"><label>Objekte (leer = alle)</label>
            <ChipSelect options={[...ALL_LABELS]} value={f.labels} onChange={(v) => set("labels", v)} color={labelColor}
              render={(l) => <span className="row" style={{ gap: 6 }}><LabelIcon label={l} />{labelName(l)}</span>} />
          </div>
          <div className="form-grid">
            <div className="field"><label>Mindest-Konfidenz</label><Range value={f.min_score} min={0.1} max={0.99} step={0.01} format={(v) => `${Math.round(v * 100)} %`} onChange={(v) => set("min_score", v)} /></div>
            <div className="field"><label>Identität</label>
              <div className="seg">
                {(["any", "known", "unknown", "specific"] as const).map((m) => (
                  <button type="button" key={m} className={f.identity.mode === m ? "on" : ""} onClick={() => set("identity", { ...f.identity, mode: m })}>
                    {{ any: "Egal", known: "Bekannt", unknown: "Unbekannt", specific: "Bestimmte" }[m]}
                  </button>
                ))}
              </div>
            </div>
          </div>
          {f.identity.mode === "specific" && (
            <div className="field"><label>Identitäten *</label>
              <SearchSelect multi inline value={f.identity.identity_ids} onChange={(v) => set("identity", { ...f.identity, identity_ids: v })}
                options={idents.map((i) => ({ value: i.id, label: i.display_name, image: i.cover_url, sub: KIND_NAME[i.kind] }))} placeholder="Identität suchen …" />
            </div>
          )}
          {(f.identity.mode === "known" || f.identity.mode === "unknown") && <div className="hint muted" style={{ fontSize: 12 }}>„Bekannt“ = Identität mit vergebenem Namen.</div>}
        </fieldset>

        <fieldset className="fieldset"><legend>Wann</legend>
          <div className="form-grid">
            <div className="field"><label>Auslöser</label>
              <div className="seg">
                {(["enter", "leave", "dwell"] as const).map((t) => (
                  <button type="button" key={t} className={f.trigger === t ? "on" : ""} onClick={() => set("trigger", t)}>
                    {{ enter: "Betreten", leave: "Verlassen", dwell: "Verweilen" }[t]}
                  </button>
                ))}
              </div>
            </div>
            {f.trigger === "dwell" ? (
              <div className="field"><label>Mindest-Verweildauer</label><Range value={f.min_dwell_s} min={1} max={600} step={1} format={(v) => `${v} s`} onChange={(v) => set("min_dwell_s", v)} /></div>
            ) : <div />}
            <div className="field"><label>Cooldown (pro Kamera)</label><Range value={f.cooldown_s} min={0} max={3600} step={5} format={(v) => (v >= 60 ? `${Math.floor(v / 60)} min${v % 60 ? ` ${v % 60} s` : ""}` : `${v} s`)} onChange={(v) => set("cooldown_s", v)} /></div>
          </div>
          <Switch checked={!!f.schedule} onChange={(v) => set("schedule", v ? { days: [0, 1, 2, 3, 4, 5, 6], start: "22:00", end: "06:00" } : null)} label="Nur zu bestimmten Zeiten" />
          {f.schedule && (
            <div className="row-wrap" style={{ gap: 16 }}>
              <ChipSelect options={[0, 1, 2, 3, 4, 5, 6]} value={f.schedule.days} onChange={(d) => set("schedule", { ...f.schedule!, days: [...d].sort() })} render={(d) => DAYS[d]} />
              <div className="row">
                <input type="time" className="input input-sm" style={{ width: 110 }} value={f.schedule.start} onChange={(e) => set("schedule", { ...f.schedule!, start: e.target.value })} />
                <span className="muted">bis</span>
                <input type="time" className="input input-sm" style={{ width: 110 }} value={f.schedule.end} onChange={(e) => set("schedule", { ...f.schedule!, end: e.target.value })} />
              </div>
              {f.schedule.start > f.schedule.end && <span className="muted" style={{ fontSize: 12 }}>über Mitternacht</span>}
            </div>
          )}
        </fieldset>

        <fieldset className="fieldset"><legend>Aktionen</legend>
          {f.actions.map((a, i) => (
            <ActionEditor key={i} a={a} onChange={(n) => set("actions", f.actions.map((x, k) => (k === i ? n : x)))} onRemove={() => set("actions", f.actions.filter((_, k) => k !== i))} />
          ))}
          <div className="row-wrap">
            {(Object.keys(ACTION_META) as RuleAction["type"][]).map((t) => (
              <button type="button" key={t} className="btn btn-sm" onClick={() => set("actions", [...f.actions, newAction(t)])}><Plus size={14} />{ACTION_META[t].name}</button>
            ))}
          </div>
        </fieldset>
      </div>
    </Dialog>
  );
}

export function RulesPage() {
  const qc = useQueryClient();
  const rules = useQuery({ queryKey: ["rules"], queryFn: api.rules });
  const cams = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const idents = useQuery({ queryKey: ["identities", "all"], queryFn: () => api.identities({}) });
  const [edit, setEdit] = useState<Rule | null | "new">(null);
  const [del, setDel] = useState<Rule | null>(null);
  const toggle = useMutation({
    mutationFn: (r: Rule) => { const { id, ...rest } = r; return api.updateRule(id, { ...rest, enabled: !r.enabled }); },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rules"] }),
    onError: (e: Error) => toast.error("Fehler", e.message),
  });
  const dup = useMutation({
    mutationFn: (r: Rule) => { const { id: _id, ...rest } = r; void _id; return api.createRule({ ...rest, name: `${r.name} (Kopie)`, enabled: false }); },
    onSuccess: () => { toast.success("Regel dupliziert"); qc.invalidateQueries({ queryKey: ["rules"] }); },
  });
  const delM = useMutation({
    mutationFn: (id: number) => api.deleteRule(id),
    onSuccess: () => { toast.success("Regel gelöscht"); qc.invalidateQueries({ queryKey: ["rules"] }); setDel(null); },
    onError: (e: Error) => toast.error("Löschen fehlgeschlagen", e.message),
  });

  return (
    <>
      <div className="page-header">
        <div><h1>Regeln</h1><div className="sub">Automatische Aktionen bei bestimmten Erkennungen</div></div>
        <button className="btn btn-primary" onClick={() => setEdit("new")}><Plus size={16} />Neue Regel</button>
      </div>
      {rules.isLoading ? <Loading /> : !rules.data?.length ? (
        <div className="card"><Empty icon={<Workflow size={44} />} title="Keine Regeln" text="Regeln lösen Snapshots, Clips, MQTT-Nachrichten oder Webhooks aus." /></div>
      ) : (
        <div className="list">
          {rules.data.map((r, i) => (
            <div key={r.id} className={`list-item ${r.enabled ? "" : "disabled"}`} style={{ animationDelay: `${i * 30}ms` }}>
              <Switch checked={r.enabled} onChange={() => toggle.mutate(r)} />
              <div className="grow">
                <div className="title">{r.name}{!r.enabled && <span className="badge">inaktiv</span>}</div>
                <div style={{ marginTop: 4 }}>{ruleSummary(r, cams.data ?? [], idents.data ?? [])}</div>
              </div>
              <div className="row" style={{ gap: 4 }}>{r.actions.map((a, k) => <span key={k} className="badge accent" title={ACTION_META[a.type].name}>{ACTION_META[a.type].icon}</span>)}</div>
              <button className="btn btn-sm" onClick={() => setEdit(r)}><Pencil size={14} />Bearbeiten</button>
              <button className="btn btn-sm btn-ghost btn-icon" title="Duplizieren" onClick={() => dup.mutate(r)}><Copy size={15} /></button>
              <button className="btn btn-sm btn-ghost btn-icon" title="Löschen" onClick={() => setDel(r)}><Trash2 size={15} /></button>
            </div>
          ))}
        </div>
      )}
      {edit && <RuleDialog rule={edit === "new" ? null : edit} cams={cams.data ?? []} idents={idents.data ?? []} onClose={() => setEdit(null)} />}
      {del && <ConfirmDialog title="Regel löschen?" busy={delM.isPending} text={<>Regel „{del.name}“ wird dauerhaft gelöscht.</>} onConfirm={() => delM.mutate(del.id)} onClose={() => setDel(null)} />}
    </>
  );
}
