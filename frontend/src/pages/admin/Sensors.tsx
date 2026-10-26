import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera as CamIcon, Gauge, History, ImagePlus, Pencil, Play, Plus, Trash2, X } from "lucide-react";
import { api } from "../../lib/api";
import type { Camera, SensorInput, SensorKind, SensorReference, VirtualSensor, VlmMeasure } from "../../lib/types";
import { ConfirmDialog, Dialog, Empty, Img, Loading, Range, Switch } from "../../components/ui";
import { REF_NAME, RegionEditor, RegionImage, SENSOR_KIND, STATE_NAME, SensorStateBadge, fmtSensorValue, vlmMeasure } from "../../components/SensorBits";
import { fmtAgo, fmtShort } from "../../lib/format";
import { toast } from "../../lib/store";
import { fmtNum, lang, useMsgs } from "../../i18n";
import type { InventoryVocabEntry } from "../../lib/types";

/**
 * Administration → Virtuelle Sensoren (W3, A3+C4): Liste mit Zustand, Editor (Bereich, Art, Parameter),
 * Referenzbilder aufnehmen, „Jetzt prüfen“, Verlauf. Vertrag: CONTRACT.md W3.F.
 */

const KINDS: SensorKind[] = ["brightness", "blink", "reference", "inventory_count", "fill", "vlm"];
const DEFAULT_INTERVAL: Record<SensorKind, number> = { brightness: 5, blink: 5, reference: 10, inventory_count: 60, fill: 60, vlm: 300 };
const DEFAULT_PARAMS: Record<SensorKind, Record<string, unknown>> = {
  brightness: { threshold: 0.5, invert: false },
  blink: { min_std: 0.05, window_s: 3 },
  reference: {},
  inventory_count: { labels: ["bottle"], min_count: 2 },
  fill: { low_below: 0.4, out_below: 0.15 },
  vlm: { measure: "yesno", question: "" },
};
const QUESTION_TEMPLATES = ["Is the light on?", "Is the window open?", "Is the stove on?", "Is the door open?", "Is there laundry on the rack?"];

// W4 (C4-VLM-Modus): Bildmodell zählt bzw. erkennt „leer“ (Vorlagen wie backend/app/vlm_measure.py)
const MEASURES: VlmMeasure[] = ["yesno", "count", "fill"];
const ITEM_TEMPLATES: Record<"count" | "fill", string[]> = {
  count: ["bottles", "eggs", "cans", "toilet paper rolls", "jars", "boxes"],
  fill: ["fruit bowl", "jar", "basket", "shelf", "bin", "firewood rack"],
};
const VLM_COUNT_MAX_MIN = 3;
const VLM_SUPPLY_MIN_AGE = 1800;
function vlmQuestionPreview(measure: "count" | "fill", item: string, question: string): string {
  if (question.trim()) return question.trim();
  const it = item.trim() || "…";
  return measure === "count" ? `How many ${it} are there? Answer with a number only.`
    : `How full is the ${it}? Answer with one word: full, half, low or empty.`;
}
/** Vorgabe für die Zählfrage aus dem Vokabular (wie backend routers/sensors.py::_vocab_item) */
function vocabItem(labels: string[], vocab: { label: string; prompts: string[] }[]): string {
  for (const l of labels) {
    const p = vocab.find((v) => v.label === l)?.prompts?.[0]?.trim();
    if (p) return p.endsWith("s") ? p : `${p}s`;
  }
  return labels[0] ? `${labels[0].replace(/_/g, " ")}s` : "";
}

/** Anzeigename eines Vokabular-Eintrags in der aktuellen Sprache (CONTRACT I18N.C) */
function vocabName(v: Pick<InventoryVocabEntry, "name_de" | "names" | "prompts">): string {
  const l = lang();
  return v.names?.[l] ?? (l === "de" ? v.name_de : v.names?.en ?? v.prompts[0] ?? v.name_de);
}

function fmtSecs(v: number) {
  if (v < 60) return `${v} s`;
  if (v < 3600) return `${Math.round(v / 60)} min`;
  return `${Math.round((v / 3600) * 10) / 10} h`;
}

function emptyInput(cams: Camera[]): SensorInput {
  return { name: "", camera_id: cams[0]?.id ?? 0, enabled: true, kind: "brightness", region: [], params: { ...DEFAULT_PARAMS.brightness },
    state_set: "binary", supply: false, verify_vlm: false, interval_s: DEFAULT_INTERVAL.brightness, vlm_max_age_s: 900,
    change_threshold: 0.08, confirm_n: 2 };
}

function References({ s }: { s: VirtualSensor }) {
  const t = useMsgs().sensors;
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["sensor-refs", s.id], queryFn: () => api.sensorReferences(s.id) });
  const states: SensorReference["state"][] = s.kind === "reference" ? ["on", "off"] : ["full", "half", "empty"];
  const add = useMutation({
    mutationFn: (st: SensorReference["state"]) => api.addSensorReference(s.id, st),
    onSuccess: (r) => { toast.success(t.refCaptured, t.refState(REF_NAME[r.state])); qc.invalidateQueries({ queryKey: ["sensor-refs", s.id] }); },
    onError: (e: Error) => toast.error(t.notCaptured, e.message),
  });
  const del = useMutation({
    mutationFn: (rid: number) => api.deleteSensorReference(s.id, rid),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sensor-refs", s.id] }),
    onError: (e: Error) => toast.error(t.notDeleted, e.message),
  });
  const refs = q.data ?? [];
  const missing = states.filter((st) => !refs.some((r) => r.state === st));
  return (
    <fieldset className="fieldset"><legend>{t.references}</legend>
      <div className="muted" style={{ fontSize: 13 }}>
        {t.refHelp}
        {s.kind === "fill" ? t.refHelpFill : t.refHelpBinary}
      </div>
      {missing.length > 0 && <div className="hint" style={{ color: "var(--warn)", fontSize: 12 }}>{t.refMissing(missing.map((m) => t.quote(REF_NAME[m])).join(", "))}</div>}
      {states.map((st) => (
        <div key={st} className="row-wrap" style={{ gap: 8, alignItems: "center" }}>
          <b style={{ width: 48 }}>{REF_NAME[st]}</b>
          {refs.filter((r) => r.state === st).map((r) => (
            <div key={r.id} style={{ position: "relative" }} title={fmtShort(r.created_at)}>
              <RegionImage src={r.image_url} region={s.region} height={64} />
              <button type="button" className="btn btn-ghost btn-icon btn-sm" style={{ position: "absolute", top: 2, right: 2, background: "rgba(0,0,0,.6)" }}
                title={t.deleteRef} onClick={() => del.mutate(r.id)}><Trash2 size={13} /></button>
            </div>
          ))}
          <button type="button" className="btn btn-sm" disabled={add.isPending || refs.filter((r) => r.state === st).length >= 5}
            onClick={() => add.mutate(st)}>{add.isPending && add.variables === st ? <span className="spinner" /> : <ImagePlus size={14} />}{t.capture(REF_NAME[st])}</button>
        </div>
      ))}
    </fieldset>
  );
}

function SensorHistoryList({ s }: { s: VirtualSensor }) {
  const t = useMsgs().sensors;
  const q = useQuery({ queryKey: ["sensor-history", s.id], queryFn: () => api.sensorHistory(s.id, 30), refetchInterval: 15000 });
  const rows = q.data ?? [];
  return (
    <fieldset className="fieldset"><legend><History size={14} style={{ verticalAlign: -2 }} /> {t.history}</legend>
      {!rows.length ? <span className="muted" style={{ fontSize: 13 }}>{t.noEntries}</span> : (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 220, overflow: "auto" }}>
          {rows.map((h, i) => (
            <div key={i} className="row" style={{ gap: 8, fontSize: 13 }}>
              <span className="muted" style={{ width: 120 }}>{fmtShort(h.ts)}</span>
              <span className="badge" style={{ minWidth: 52, justifyContent: "center" }}>{STATE_NAME[h.state]}</span>
              <span className="muted">{fmtSensorValue({ kind: s.kind, value: h.value, params: s.params })}</span>
              {h.engine && h.engine !== "cheap" && <span className="badge info">{h.engine === "cloud" ? t.cloud : t.imageModel}</span>}
              {h.snapshot_url && <a href={h.snapshot_url} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>{t.image}</a>}
            </div>
          ))}
        </div>
      )}
    </fieldset>
  );
}

function SensorDialog({ sensor, cams, onClose }: { sensor: VirtualSensor | null; cams: Camera[]; onClose: () => void }) {
  const mm = useMsgs();
  const t = mm.sensors;
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const vocab = settings.data?.inventory_vocabulary ?? [];
  const [f, setF] = useState<SensorInput>(() => {
    if (!sensor) return emptyInput(cams);
    const { id: _i, camera_name: _c, room: _r, state: _s, value: _v, since: _si, last_eval: _l, last_engine: _e, snapshot_url: _sn, state_text: _t, last_answer: _a, ...rest } = sensor;
    void _i; void _c; void _r; void _s; void _v; void _si; void _l; void _e; void _sn; void _t; void _a;
    return { ...rest, params: { ...rest.params } };
  });
  const set = <K extends keyof SensorInput>(k: K, v: SensorInput[K]) => setF((p) => ({ ...p, [k]: v }));
  const p = f.params as Record<string, any>;  // eslint-disable-line @typescript-eslint/no-explicit-any
  const setP = (patch: Record<string, unknown>) => setF((x) => ({ ...x, params: { ...x.params, ...patch } }));
  const setKind = (k: SensorKind) => setF((x) => ({
    ...x, kind: k, params: { ...DEFAULT_PARAMS[k], ...(x.params.question ? { question: x.params.question } : {}) },
    interval_s: DEFAULT_INTERVAL[k], state_set: k === "fill" ? "level" : k === "inventory_count" ? x.state_set : "binary",
    supply: k === "fill" || k === "inventory_count" ? x.supply : false,
    verify_vlm: ["brightness", "blink", "reference"].includes(k) ? x.verify_vlm : false,
    vlm_max_age_s: k === "vlm" ? 900 : x.vlm_max_age_s,
  }));
  // W4 (C4-VLM): Messart wechseln – Vorräte per Bildmodell selten fragen (≥ 30 min ohne sichtbare Änderung)
  const setMeasure = (m: VlmMeasure) => setF((x) => ({
    ...x,
    params: m === "yesno" ? { measure: "yesno", question: "" }
      : m === "count" ? { measure: "count", item: String(x.params.item ?? "bottles"), min_count: 1 }
      : { measure: "fill", item: String(x.params.item ?? "fruit bowl") },
    state_set: m === "yesno" ? "binary" : "level",
    supply: m !== "yesno",
    interval_s: m === "yesno" ? DEFAULT_INTERVAL.vlm : 1800,
    vlm_max_age_s: m === "yesno" ? 900 : 3600,
  }));
  const cam = cams.find((c) => c.id === f.camera_id) ?? null;
  const measure = vlmMeasure(f);
  const vlmSupply = f.kind === "vlm" && (measure === "count" || measure === "fill");
  const countVerify = f.kind === "inventory_count" && f.verify_vlm;
  const level = f.kind === "fill" || (f.kind === "inventory_count" && f.state_set === "level")
    || (f.kind === "vlm" && (measure === "fill" || (measure === "count" && f.state_set === "level")));
  const needsQuestion = (f.kind === "vlm" && measure === "yesno") || (f.verify_vlm && ["brightness", "blink", "reference"].includes(f.kind));
  const usesVlm = f.kind === "vlm" || f.verify_vlm;
  const itemOk = !(vlmSupply || countVerify) || String(p.item ?? "").trim() || String(p.question ?? "").trim() || countVerify;
  const valid = f.name.trim() && f.camera_id && f.region.length >= 3 && (!needsQuestion || String(p.question ?? "").trim())
    && (f.kind !== "inventory_count" || (p.labels ?? []).length > 0) && (f.kind !== "vlm" || f.interval_s >= 60)
    && itemOk && (!vlmSupply || f.vlm_max_age_s >= VLM_SUPPLY_MIN_AGE)
    && (!countVerify || Number(p.min_count ?? 1) <= VLM_COUNT_MAX_MIN);
  const save = useMutation({
    mutationFn: () => {
      const body: SensorInput = { ...f, name: f.name.trim(), supply: level ? f.supply : false };
      return sensor ? api.updateSensor(sensor.id, body) : api.createSensor(body);
    },
    onSuccess: (s) => {
      toast.success(t.saved, s.name);
      qc.invalidateQueries({ queryKey: ["sensors"] });
      if (!sensor && (s.kind === "reference" || s.kind === "fill")) toast.info(t.captureRefsNow, t.captureRefsHow);
      onClose();
    },
    onError: (e: Error) => toast.error(t.saveFailed, e.message),
  });

  return (
    <Dialog size="xl" onClose={onClose} icon={<Gauge size={18} />} title={sensor ? t.dialogTitle(sensor.name) : t.newSensor} footer={<>
      <div className="spacer muted" style={{ fontSize: 13 }}>{SENSOR_KIND[f.kind].text}</div>
      <button className="btn btn-ghost" onClick={onClose}>{mm.common.cancel}</button>
      <button className="btn btn-primary" disabled={!valid || save.isPending} onClick={() => save.mutate()}>{save.isPending && <span className="spinner" />}{mm.common.save}</button>
    </>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div className="form-grid">
          <div className="field"><label>{t.name}</label><input className="input" autoFocus value={f.name} onChange={(e) => set("name", e.target.value)} placeholder={t.namePlaceholder} /></div>
          <div className="field"><label>{t.camera}</label>
            <select className="select" value={f.camera_id || ""} onChange={(e) => set("camera_id", Number(e.target.value))}>
              {cams.map((c) => <option key={c.id} value={c.id}>{c.name}{c.room ? ` · ${c.room}` : ""}</option>)}
            </select>
          </div>
        </div>
        <div className="field"><label>{t.kindLabel}</label>
          <div className="seg" style={{ flexWrap: "wrap" }}>
            {KINDS.map((k) => (
              <button type="button" key={k} className={f.kind === k ? "on" : ""} onClick={() => setKind(k)} title={SENSOR_KIND[k].text}>
                <span className="row" style={{ gap: 6 }}>{SENSOR_KIND[k].icon}{SENSOR_KIND[k].name}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="sensor-edit-grid">
          <div className="field"><label>{t.region}</label>
            <RegionEditor cameraId={cam?.id ?? null} region={f.region} onChange={(r) => set("region", r)} />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {f.kind === "brightness" && <>
              <div className="field"><label>{t.onFromBrightness}</label>
                <Range value={Number(p.threshold ?? 0.5)} min={0.05} max={0.95} step={0.01} format={(v) => `${Math.round(v * 100)} %`} onChange={(v) => setP({ threshold: v })} /></div>
              <Switch checked={!!p.invert} onChange={(v) => setP({ invert: v })} label={p.invert ? t.inverted : t.brightIsOn} />
            </>}
            {f.kind === "blink" && <>
              <div className="field"><label>{t.minFluctuation}</label>
                <Range value={Number(p.min_std ?? 0.05)} min={0.01} max={0.3} step={0.005} format={(v) => fmtNum(v, 3)} onChange={(v) => setP({ min_std: v })} /></div>
              <div className="field"><label>{t.timeWindow}</label>
                <Range value={Number(p.window_s ?? 3)} min={1} max={15} step={1} format={(v) => `${v} s`} onChange={(v) => setP({ window_s: v })} /></div>
            </>}
            {f.kind === "inventory_count" && <>
              <div className="field"><label>{t.items}</label>
                <div className="row-wrap" style={{ gap: 6 }}>
                  {((p.labels ?? []) as string[]).map((l) => (
                    <button type="button" key={l} className="chip on" title={mm.common.remove}
                      onClick={() => setP({ labels: ((p.labels ?? []) as string[]).filter((x) => x !== l) })}>
                      {(() => { const v = vocab.find((x) => x.label === l); return v ? vocabName(v) : l; })()}<X size={12} />
                    </button>
                  ))}
                  <select className="select input-sm" style={{ width: "auto", minWidth: 150 }} value=""
                    onChange={(e) => e.target.value && setP({ labels: [...((p.labels ?? []) as string[]), e.target.value] })}>
                    <option value="">{t.addItem}</option>
                    {vocab.filter((v) => !((p.labels ?? []) as string[]).includes(v.label)).map((v) => <option key={v.label} value={v.label}>{vocabName(v)}</option>)}
                  </select>
                </div>
                <span className="hint muted" style={{ fontSize: 12 }}>{t.itemsHint}</span>
              </div>
              <div className="field"><label>{level ? t.okFromCount : t.onFromCount}</label>
                <Range value={Number(p.min_count ?? 1)} min={1} max={20} step={1} format={(v) => `${v}`} onChange={(v) => setP({ min_count: v })} /></div>
              <div className="field"><label>{t.states}</label>
                <div className="seg">
                  <button type="button" className={f.state_set === "binary" ? "on" : ""} onClick={() => setF((x) => ({ ...x, state_set: "binary", supply: false }))}>{t.statesBinary}</button>
                  <button type="button" className={f.state_set === "level" ? "on" : ""} onClick={() => set("state_set", "level")}>{t.statesLevel}</button>
                </div>
              </div>
              {cam && !cam.inventory_enabled && <div className="hint" style={{ color: "var(--warn)", fontSize: 12 }}>{t.inventoryOff}</div>}
              <Switch checked={f.verify_vlm} onChange={(v) => setF((x) => ({ ...x, verify_vlm: v,
                params: v && !x.params.item ? { ...x.params, item: vocabItem((x.params.labels ?? []) as string[], vocab) } : x.params }))}
                label={t.verifyCount} />
              {countVerify && <>
                <div className="field"><label>{t.countItem}</label>
                  <input className="input" value={String(p.item ?? "")} maxLength={80} onChange={(e) => setP({ item: e.target.value })} placeholder="bottles" />
                  <span className="hint muted" style={{ fontSize: 12 }}>{t.countQuestionHint(vlmQuestionPreview("count", String(p.item ?? ""), ""))}</span>
                </div>
                {Number(p.min_count ?? 1) > VLM_COUNT_MAX_MIN && <span className="hint" style={{ color: "var(--bad)", fontSize: 12 }}>{t.countMaxHint(VLM_COUNT_MAX_MIN)}</span>}
              </>}
            </>}
            {f.kind === "fill" && <>
              <div className="field"><label>{t.lowBelow}</label>
                <Range value={Number(p.low_below ?? 0.4)} min={0.1} max={0.9} step={0.05} format={(v) => `${Math.round(v * 100)} %`} onChange={(v) => setP({ low_below: v })} /></div>
              <div className="field"><label>{t.outBelow}</label>
                <Range value={Number(p.out_below ?? 0.15)} min={0.05} max={0.8} step={0.05} format={(v) => `${Math.round(v * 100)} %`} onChange={(v) => setP({ out_below: v })} /></div>
              {Number(p.out_below) >= Number(p.low_below) && <span className="hint" style={{ color: "var(--bad)", fontSize: 12 }}>{t.outBelowLow}</span>}
            </>}
            {f.kind === "vlm" && (
              <div className="field"><label>{t.measure}</label>
                <div className="seg">
                  {MEASURES.map((m) => <button type="button" key={m} className={measure === m ? "on" : ""} onClick={() => setMeasure(m)}>{t.measureName[m]}</button>)}
                </div>
              </div>
            )}
            {vlmSupply && (measure === "count" || measure === "fill") && <>
              <div className="field"><label>{t.itemEn}</label>
                <input className="input" value={String(p.item ?? "")} maxLength={80} onChange={(e) => setP({ item: e.target.value })}
                  placeholder={measure === "count" ? "bottles" : "fruit bowl"} />
                <div className="placeholders">{ITEM_TEMPLATES[measure].map((t) => <code key={t} onClick={() => setP({ item: t })}>{t}</code>)}</div>
              </div>
              <div className="field"><label>{t.ownQuestion}</label>
                <input className="input" value={String(p.question ?? "")} maxLength={300} onChange={(e) => setP({ question: e.target.value })}
                  placeholder={vlmQuestionPreview(measure, String(p.item ?? ""), "")} />
                <span className="hint muted" style={{ fontSize: 12 }}>{t.questionToModel(vlmQuestionPreview(measure, String(p.item ?? ""), String(p.question ?? "")))}</span>
              </div>
              {measure === "count" ? <>
                <div className="field"><label>{level ? t.okFromCount : t.onFromCount}</label>
                  <Range value={Math.min(VLM_COUNT_MAX_MIN, Number(p.min_count ?? 1))} min={1} max={VLM_COUNT_MAX_MIN} step={1} format={(v) => `${v}`} onChange={(v) => setP({ min_count: v })} /></div>
                <div className="field"><label>{t.states}</label>
                  <div className="seg">
                    <button type="button" className={f.state_set === "binary" ? "on" : ""} onClick={() => setF((x) => ({ ...x, state_set: "binary", supply: false }))}>{t.statesBinary}</button>
                    <button type="button" className={f.state_set === "level" ? "on" : ""} onClick={() => set("state_set", "level")}>{t.statesLevel}</button>
                  </div>
                </div>
                <span className="hint muted" style={{ fontSize: 12 }}>{t.countLimitHint(VLM_COUNT_MAX_MIN)}</span>
              </> : (
                <span className="hint muted" style={{ fontSize: 12 }}>{t.fillLimitHint}</span>
              )}
            </>}
            {(f.kind === "vlm" || ["brightness", "blink", "reference"].includes(f.kind)) && <>
              {f.kind !== "vlm" && (
                <Switch checked={f.verify_vlm} onChange={(v) => set("verify_vlm", v)} label={t.verifyChange} />
              )}
              {needsQuestion && (
                <div className="field"><label>{t.question}</label>
                  <input className="input" value={String(p.question ?? "")} maxLength={300} onChange={(e) => setP({ question: e.target.value })} placeholder="Is the light on?" />
                  <div className="placeholders">{QUESTION_TEMPLATES.map((q) => <code key={q} onClick={() => setP({ question: q })}>{q}</code>)}</div>
                  <span className="hint muted" style={{ fontSize: 12 }}>{t.questionHint(f.kind !== "vlm")}</span>
                </div>
              )}
            </>}
            {level && (
              <Switch checked={f.supply} onChange={(v) => set("supply", v)} label={t.supply} />
            )}
            <div className="field"><label>{t.checkEvery}</label>
              <Range value={f.interval_s} min={f.kind === "vlm" ? 60 : 2} max={f.kind === "vlm" ? 3600 : 600} step={f.kind === "vlm" ? 30 : 1} format={fmtSecs} onChange={(v) => set("interval_s", v)} /></div>
            <div className="field"><label>{t.confirmN}</label>
              <Range value={f.confirm_n} min={1} max={10} step={1} format={(v) => `${v}×`} onChange={(v) => set("confirm_n", v)} /></div>
            {usesVlm && <>
              <div className="field"><label>{t.vlmMaxAge}</label>
                <Range value={f.vlm_max_age_s} min={vlmSupply ? VLM_SUPPLY_MIN_AGE : 60} max={21600} step={60} format={fmtSecs} onChange={(v) => set("vlm_max_age_s", v)} />
                {vlmSupply && <span className="hint muted" style={{ fontSize: 12 }}>{t.vlmSupplyHint}</span>}
              </div>
              {f.kind !== "inventory_count" && (  /* Zählsensoren: kein Änderungsmaß vom Worker */
                <div className="field"><label>{t.changeThreshold}</label>
                  <Range value={f.change_threshold} min={0.02} max={0.5} step={0.01} format={(v) => `${Math.round(v * 100)} %`} onChange={(v) => set("change_threshold", v)} /></div>
              )}
            </>}
            <Switch checked={f.enabled} onChange={(v) => set("enabled", v)} label={f.enabled ? t.active : t.off} />
          </div>
        </div>
        {sensor && (sensor.kind === "reference" || sensor.kind === "fill") && sensor.kind === f.kind && <References s={{ ...sensor, region: f.region }} />}
        {sensor && <SensorHistoryList s={sensor} />}
      </div>
    </Dialog>
  );
}

function SensorRow({ s, onEdit, onDelete }: { s: VirtualSensor; onEdit: () => void; onDelete: () => void }) {
  const mm = useMsgs();
  const t = mm.sensors;
  const qc = useQueryClient();
  const evalM = useMutation({
    mutationFn: () => api.evaluateSensor(s.id),
    onSuccess: (r) => { toast.info(`${r.name}: ${STATE_NAME[r.state]}`, r.state_text); qc.invalidateQueries({ queryKey: ["sensors"] }); },
    onError: (e: Error) => toast.error(t.checkFailed, e.message),
  });
  const toggle = useMutation({
    mutationFn: () => {
      const { id, camera_name: _c, room: _r, state: _s, value: _v, since: _si, last_eval: _l, last_engine: _e, snapshot_url: _sn, state_text: _t, last_answer: _a, ...rest } = s;
      void _c; void _r; void _s; void _v; void _si; void _l; void _e; void _sn; void _t; void _a;
      return api.updateSensor(id, { ...rest, enabled: !s.enabled });
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sensors"] }),
    onError: (e: Error) => toast.error(mm.common.error, e.message),
  });
  return (
    <div className={`list-item ${s.enabled ? "" : "disabled"}`}>
      <Switch checked={s.enabled} onChange={() => toggle.mutate()} />
      {s.snapshot_url ? <Img src={s.snapshot_url} className="sensor-thumb" /> : <RegionImage src={null} region={s.region} height={46} />}
      <div className="grow">
        <div className="title">{s.name} <SensorStateBadge s={s} />{s.supply && <span className="badge accent">{t.supplyBadge}</span>}</div>
        <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>
          <span className="row" style={{ gap: 6, display: "inline-flex" }}>{SENSOR_KIND[s.kind].icon}{SENSOR_KIND[s.kind].name}</span>
          {" · "}{s.state_text}{" · "}{fmtSensorValue(s)}
          {s.last_eval && <> · {t.checked(fmtAgo(s.last_eval))}</>}
          {s.last_engine && s.last_engine !== "cheap" && <> · {s.last_engine === "cloud" ? t.cloud : t.imageModel}</>}
          {s.last_answer && <span title={t.lastAnswer}> · {t.quote(s.last_answer.length > 80 ? `${s.last_answer.slice(0, 80)}…` : s.last_answer)}</span>}
        </div>
      </div>
      <button className="btn btn-sm" disabled={evalM.isPending || !s.enabled} onClick={() => evalM.mutate()} title={t.evaluateTitle}>
        {evalM.isPending ? <span className="spinner" /> : <Play size={14} />}{t.checkNow}</button>
      <button className="btn btn-sm" onClick={onEdit}><Pencil size={14} />{mm.common.edit}</button>
      <button className="btn btn-sm btn-ghost btn-icon" title={mm.common.delete} onClick={onDelete}><Trash2 size={15} /></button>
    </div>
  );
}

export function SensorsPage() {
  const t = useMsgs().sensors;
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["sensors"], queryFn: () => api.sensors(), refetchInterval: 20000 });
  const cams = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const [edit, setEdit] = useState<VirtualSensor | null | "new">(null);
  const [del, setDel] = useState<VirtualSensor | null>(null);
  const delM = useMutation({
    mutationFn: (id: number) => api.deleteSensor(id),
    onSuccess: () => { toast.success(t.deleted); qc.invalidateQueries({ queryKey: ["sensors"] }); setDel(null); },
    onError: (e: Error) => toast.error(t.deleteFailed, e.message),
  });
  const groups = useMemo(() => {
    const g = new Map<number, VirtualSensor[]>();
    for (const s of q.data ?? []) g.set(s.camera_id, [...(g.get(s.camera_id) ?? []), s]);
    return [...g.entries()];
  }, [q.data]);
  if (q.isLoading || cams.isLoading) return <Loading />;
  const camList = cams.data ?? [];
  return (
    <div>
      <div className="page-header">
        <div><h1>{t.title}</h1><div className="sub">{t.subtitle}</div></div>
        <button className="btn btn-primary" disabled={!camList.length} onClick={() => setEdit("new")}><Plus size={16} />{t.newSensor}</button>
      </div>
      {!q.data?.length ? (
        <div className="card"><Empty icon={<Gauge size={44} />} title={t.emptyTitle}
          text={t.emptyText} /></div>
      ) : groups.map(([cid, list]) => (
        <div key={cid} style={{ marginBottom: 18 }}>
          <div className="row muted" style={{ gap: 6, margin: "0 0 8px 2px", fontSize: 13 }}><CamIcon size={14} />{list[0].camera_name}{list[0].room ? ` · ${list[0].room}` : ""}</div>
          <div className="list">
            {list.map((s) => <SensorRow key={s.id} s={s} onEdit={() => setEdit(s)} onDelete={() => setDel(s)} />)}
          </div>
        </div>
      ))}
      {edit && <SensorDialog sensor={edit === "new" ? null : edit} cams={camList} onClose={() => setEdit(null)} />}
      {del && <ConfirmDialog title={t.deleteTitle} busy={delM.isPending}
        text={t.deleteText(del.name)}
        onConfirm={() => delM.mutate(del.id)} onClose={() => setDel(null)} />}
    </div>
  );
}
