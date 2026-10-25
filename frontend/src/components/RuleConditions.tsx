import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleHelp, CloudRain, Footprints, Hand, Package, Play, Plus, X, XCircle } from "lucide-react";
import { api } from "../lib/api";
import type { Camera, InventoryVocabEntry, RuleCheckResult, RuleCondition, RuleInput } from "../lib/types";
import { Range } from "./ui";

/**
 * A1: Block „Nur wenn …“ im Regel-Editor (Bedingungen, Verzögerung, „Jetzt prüfen“) und Vorlagen.
 * Bedingungstypen laut CONTRACT.md W1.B; alle müssen erfüllt sein.
 */

type CondType = RuleCondition["type"];

export const COND_META: Record<CondType, { name: string; icon: React.ReactNode; trackOnly?: boolean }> = {
  inventory: { name: "Gegenstand da/weg", icon: <Package size={15} /> },
  inventory_untouched: { name: "Gegenstand unberührt", icon: <Hand size={15} /> },
  weather: { name: "Regen", icon: <CloudRain size={15} /> },
  track_came_from: { name: "Kam aus Zone", icon: <Footprints size={15} />, trackOnly: true },
};

export function newCondition(t: CondType): RuleCondition {
  switch (t) {
    case "inventory": return { type: "inventory", labels: ["keys"], state: "present", camera_ids: [], room: null, zone_ids: [] };
    case "inventory_untouched": return { type: "inventory_untouched", labels: ["medicine"], since: "06:00", camera_ids: [], zone_ids: [] };
    case "weather": return { type: "weather", metric: "rain_probability", min: 60, hours: 12 };
    case "track_came_from": return { type: "track_came_from", zone_ids: [] };
  }
}

function useVocab(): InventoryVocabEntry[] {
  const st = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  return st.data?.inventory_vocabulary ?? [];
}

function vocabName(vocab: InventoryVocabEntry[], label: string) {
  return vocab.find((v) => v.label === label)?.name_de ?? label;
}

function joinUnd(xs: string[]) {
  if (xs.length <= 1) return xs.join("");
  return `${xs.slice(0, -1).join(", ")} und ${xs[xs.length - 1]}`;
}

/** Kurzer deutscher Text je Bedingung (Regel-Übersicht) */
export function conditionText(c: RuleCondition, vocab: InventoryVocabEntry[], cams: Camera[]): string {
  const zoneName = (id: string) => cams.flatMap((x) => x.zones).find((z) => z.id === id)?.name ?? id;
  switch (c.type) {
    case "inventory": {
      const where = c.zone_ids.length ? ` in ${joinUnd(c.zone_ids.map(zoneName))}` : c.room ? ` in ${c.room}` : "";
      return `${joinUnd(c.labels.map((l) => vocabName(vocab, l)))} ${c.state === "present" ? "noch da" : "weg"}${where}`;
    }
    case "inventory_untouched": return `${joinUnd(c.labels.map((l) => vocabName(vocab, l)))} seit ${c.since} unberührt`;
    case "weather": return `Regen ≥ ${c.min} % in ${c.hours} h`;
    case "track_came_from": return `kam aus ${joinUnd(c.zone_ids.map(zoneName)) || "?"}`;
  }
}

export function useConditionSummary(conds: RuleCondition[], cams: Camera[]): string {
  const vocab = useVocab();
  return conds.map((c) => conditionText(c, vocab, cams)).join(" und ");
}

/** Mehrfachauswahl aus dem Gegenstands-Vokabular (gruppiert) */
function LabelPicker({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const vocab = useVocab();
  const groups = useMemo(() => {
    const g = new Map<string, InventoryVocabEntry[]>();
    for (const v of vocab) {
      if (value.includes(v.label)) continue;
      const k = v.group || "Weitere";
      g.set(k, [...(g.get(k) ?? []), v]);
    }
    return [...g.entries()];
  }, [vocab, value]);
  return (
    <div className="row-wrap" style={{ gap: 6 }}>
      {value.map((l) => (
        <span key={l} className="chip on">{vocabName(vocab, l)}
          {value.length > 1 && <button type="button" className="btn btn-ghost btn-icon btn-sm" style={{ width: 18, height: 18, padding: 0 }} title="Entfernen"
            onClick={() => onChange(value.filter((x) => x !== l))}><X size={12} /></button>}
        </span>
      ))}
      <select className="select input-sm" style={{ width: "auto", minWidth: 150 }} value=""
        onChange={(e) => e.target.value && onChange([...value, e.target.value])}>
        <option value="">+ Gegenstand …</option>
        {groups.map(([g, items]) => (
          <optgroup key={g} label={g}>{items.map((v) => <option key={v.label} value={v.label}>{v.name_de}</option>)}</optgroup>
        ))}
      </select>
    </div>
  );
}

function ZonePicker({ cams, cameraIds, value, onChange, empty }: { cams: Camera[]; cameraIds: number[]; value: string[]; onChange: (v: string[]) => void; empty: string }) {
  const zones = cams.filter((c) => !cameraIds.length || cameraIds.includes(c.id)).flatMap((c) => c.zones.map((z) => ({ ...z, cam: c.name })));
  if (!zones.length) return <span className="muted" style={{ fontSize: 13 }}>Keine Zonen definiert.</span>;
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
  return (
    <div className="chips">
      {cams.map((c) => {
        const on = value.includes(c.id);
        return (
          <button type="button" key={c.id} className={`chip ${on ? "on" : ""}`} onClick={() => onChange(on ? value.filter((v) => v !== c.id) : [...value, c.id])}>
            {c.name}{c.inventory_enabled ? "" : <span className="muted"> · ohne Inventar</span>}
          </button>
        );
      })}
    </div>
  );
}

function ConditionEditor({ c, cams, onChange, onRemove }: { c: RuleCondition; cams: Camera[]; onChange: (c: RuleCondition) => void; onRemove: () => void }) {
  const meta = COND_META[c.type];
  const rooms = [...new Set(cams.map((x) => x.room).filter((r): r is string => !!r))];
  return (
    <div className="action-card">
      <div className="ah"><span className="ico">{meta.icon}</span>{meta.name}<span className="spacer" />
        <button type="button" className="btn btn-ghost btn-icon btn-sm" onClick={onRemove} title="Bedingung entfernen"><X size={15} /></button>
      </div>
      {c.type === "inventory" && (
        <div className="form-grid">
          <div className="field span-2"><label>Gegenstand</label><LabelPicker value={c.labels} onChange={(v) => onChange({ ...c, labels: v })} /></div>
          <div className="field"><label>ist</label>
            <div className="seg">
              <button type="button" className={c.state === "present" ? "on" : ""} onClick={() => onChange({ ...c, state: "present" })}>noch da</button>
              <button type="button" className={c.state === "absent" ? "on" : ""} onClick={() => onChange({ ...c, state: "absent" })}>weg</button>
            </div>
          </div>
          <div className="field"><label>im Raum</label>
            <select className="select" value={c.room ?? ""} onChange={(e) => onChange({ ...c, room: e.target.value || null })}>
              <option value="">egal</option>
              {rooms.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
          <div className="field span-2"><label>Kameras (leer = alle)</label><CameraPicker cams={cams} value={c.camera_ids} onChange={(v) => onChange({ ...c, camera_ids: v })} /></div>
          <div className="field span-2"><label>in Zone (Ort des Gegenstands)</label>
            <ZonePicker cams={cams} cameraIds={c.camera_ids} value={c.zone_ids} onChange={(v) => onChange({ ...c, zone_ids: v })} empty="leer = überall" />
          </div>
          <div className="hint muted span-2" style={{ fontSize: 12 }}>
            „weg“ heißt: kein passender Gegenstand ist gerade sichtbar (auch wenn er nie erkannt wurde). Kleine Dinge wie Schlüssel
            werden nicht immer sicher erkannt – vorher unter „Gegenstände“ prüfen, ob die Kamera sie zuverlässig findet.
          </div>
        </div>
      )}
      {c.type === "inventory_untouched" && (
        <div className="form-grid">
          <div className="field span-2"><label>Gegenstand</label><LabelPicker value={c.labels} onChange={(v) => onChange({ ...c, labels: v })} /></div>
          <div className="field"><label>unberührt seit (heute)</label>
            <input type="time" className="input" value={c.since} onChange={(e) => e.target.value && onChange({ ...c, since: e.target.value })} />
          </div>
          <div className="field"><label>Kameras (leer = alle)</label><CameraPicker cams={cams} value={c.camera_ids} onChange={(v) => onChange({ ...c, camera_ids: v })} /></div>
          <div className="field span-2"><label>und niemand war in Zone (optional)</label>
            <ZonePicker cams={cams} cameraIds={c.camera_ids} value={c.zone_ids} onChange={(v) => onChange({ ...c, zone_ids: v })} empty="leer = ohne Personenprüfung" />
          </div>
          <div className="hint muted span-2" style={{ fontSize: 12 }}>
            Unberührt = seit der Uhrzeit nicht verschoben und nicht länger als 2 min verschwunden. Das ist nur ein Hinweis, kein
            Beweis. Am zuverlässigsten bei gut sichtbaren Gegenständen (Erkennungs-Score ab etwa 60 %).
          </div>
        </div>
      )}
      {c.type === "weather" && (
        <div className="form-grid">
          <div className="field"><label>Regenwahrscheinlichkeit mindestens</label>
            <Range value={c.min} min={0} max={100} step={5} format={(v) => `${v} %`} onChange={(v) => onChange({ ...c, min: v })} /></div>
          <div className="field"><label>in den nächsten</label>
            <Range value={c.hours} min={1} max={48} step={1} format={(v) => `${v} h`} onChange={(v) => onChange({ ...c, hours: v })} /></div>
          <div className="hint muted span-2" style={{ fontSize: 12 }}>
            Wetterdaten von Open-Meteo (ohne Konto). Der Standort wird unter Einstellungen → Benachrichtigungen eingestellt;
            ohne Standort oder ohne Verbindung ist die Bedingung nie erfüllt.
          </div>
        </div>
      )}
      {c.type === "track_came_from" && (
        <div className="form-grid">
          <div className="field span-2"><label>Die auslösende Person/das Objekt war vorher in Zone</label>
            <ZonePicker cams={cams} cameraIds={[]} value={c.zone_ids} onChange={(v) => onChange({ ...c, zone_ids: v })} empty="mindestens eine Zone wählen" />
          </div>
          <div className="hint muted span-2" style={{ fontSize: 12 }}>
            Unterscheidet „geht hinaus“ (kam aus dem Flur) von „kommt heim“. Taucht der Track direkt in beiden Zonen auf, ist die
            Richtung unklar und die Bedingung nicht erfüllt.
          </div>
        </div>
      )}
    </div>
  );
}

function CheckResult({ r }: { r: RuleCheckResult }) {
  return (
    <div className="card" style={{ background: "var(--bg-3)" }}>
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 13 }}>
        <b style={{ color: r.ok ? "var(--ok)" : "var(--bad)" }}>{r.ok ? "Bedingungen sind jetzt erfüllt" : "Bedingungen sind jetzt nicht erfüllt"}</b>
        {r.details.map((d, i) => (
          <div key={i} className="row" style={{ gap: 6, alignItems: "flex-start" }}>
            {d.ok === null ? <CircleHelp size={15} className="muted" /> : d.ok ? <CheckCircle2 size={15} style={{ color: "var(--ok)" }} /> : <XCircle size={15} style={{ color: "var(--bad)" }} />}
            <span><b>{COND_META[d.type]?.name ?? d.type}:</b> {d.reason}</span>
          </div>
        ))}
        {r.preview && <div className="muted">Vorschau Push: <b>{r.preview.title}</b> – {r.preview.message}</div>}
      </div>
    </div>
  );
}

export function ConditionsBlock({ f, cams, onChange }: { f: RuleInput; cams: Camera[]; onChange: (p: Partial<RuleInput>) => void }) {
  const isTime = f.trigger === "time";
  const [result, setResult] = useState<RuleCheckResult | null>(null);
  const check = useMutation({
    mutationFn: () => api.checkRule({ ...f, name: f.name.trim() || "Entwurf" }),
    onSuccess: setResult,
    onError: (e: Error) => setResult({ ok: false, reason: e.message, details: [], placeholders: { items: "", item_place: "", weather: "" }, item_ids: [], preview: null }),
  });
  const set = (conds: RuleCondition[]) => { setResult(null); onChange({ conditions: conds }); };
  const types = (Object.keys(COND_META) as CondType[]).filter((t) => !(isTime && COND_META[t].trackOnly));
  return (
    <fieldset className="fieldset"><legend>Nur wenn …</legend>
      {!f.conditions.length && (
        <div className="muted" style={{ fontSize: 13 }}>
          Ohne Bedingungen löst die Regel {isTime ? "zur Uhrzeit immer" : "bei jedem Auslöser"} aus. Mit Bedingungen entsteht zusätzlich ein Eintrag unter „Hinweise“.
        </div>
      )}
      {f.conditions.map((c, i) => (
        <ConditionEditor key={i} c={c} cams={cams}
          onChange={(n) => set(f.conditions.map((x, k) => (k === i ? n : x)))}
          onRemove={() => set(f.conditions.filter((_, k) => k !== i))} />
      ))}
      <div className="row-wrap">
        {types.map((t) => (
          <button type="button" key={t} className="btn btn-sm" onClick={() => set([...f.conditions, newCondition(t)])}><Plus size={14} />{COND_META[t].name}</button>
        ))}
        {f.conditions.length > 0 && (
          <button type="button" className="btn btn-sm btn-ghost" disabled={check.isPending} onClick={() => check.mutate()}>
            {check.isPending ? <span className="spinner" /> : <Play size={14} />}Jetzt prüfen
          </button>
        )}
      </div>
      {f.conditions.length > 0 && (
        <div className="field"><label>{isTime ? "Prüfen nach der Uhrzeit um" : "Prüfen nach dem Auslösen um"}</label>
          <Range value={f.condition_delay_s} min={0} max={600} step={5}
            format={(v) => (v >= 60 ? `${Math.floor(v / 60)} min${v % 60 ? ` ${v % 60} s` : ""}` : `${v} s`)}
            onChange={(v) => onChange({ condition_delay_s: v })} />
          {!isTime && f.conditions.some((c) => c.type === "inventory") && f.condition_delay_s < 120 && (
            <span className="hint" style={{ color: "var(--warn)" }}>
              Empfehlung: mindestens 2 min. Ein weggenommener Gegenstand gilt erst nach einigen Analysen als „weg“, und die
              Erkennung flackert gelegentlich.
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

export const RULE_TEMPLATES: RuleTemplate[] = [
  {
    id: "keys", name: "Schlüssel vergessen", text: "Person geht zur Haustür, der Schlüssel liegt aber noch da",
    build: (b) => ({ ...b, name: "Schlüssel vergessen", labels: ["person"], trigger: "enter", trigger_options: {}, cooldown_s: 600,
      conditions: [newCondition("inventory")], condition_delay_s: 120,
      actions: [pushAction("{items} liegt noch da ({item_place})")] }),
  },
  {
    id: "umbrella", name: "Regenschirm", text: "Person geht zur Haustür, es soll regnen und der Schirm hängt noch",
    build: (b) => ({ ...b, name: "Regenschirm mitnehmen", labels: ["person"], trigger: "enter", trigger_options: {}, cooldown_s: 1800,
      conditions: [{ ...newCondition("inventory"), labels: ["umbrella"] } as RuleCondition, newCondition("weather")], condition_delay_s: 120,
      actions: [pushAction("Regenschirm mitnehmen – {weather}")] }),
  },
  {
    id: "medicine", name: "Medikamente", text: "Täglich um 10:00, wenn die Medikamente seit dem Morgen unberührt sind",
    build: (b) => ({ ...b, name: "Medikamente", trigger: "time", trigger_options: { at: "10:00" }, cooldown_s: 0, schedule: null,
      camera_ids: [], zone_ids: [], labels: [], identity: { mode: "any", identity_ids: [] },
      conditions: [newCondition("inventory_untouched")], condition_delay_s: 0,
      actions: [pushAction("{items} heute noch nicht genommen? Liegen unberührt seit dem Morgen ({item_place}).")] }),
  },
];
