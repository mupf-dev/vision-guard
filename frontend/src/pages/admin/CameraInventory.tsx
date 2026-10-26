import { groupName } from "../../lib/labels";
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, EyeOff, Package, RotateCcw, SlidersHorizontal, Trash2 } from "lucide-react";
import { api } from "../../lib/api";
import type { CameraInput, InventorySuppress, InventoryVocabEntry } from "../../lib/types";
import { Range, Switch } from "../../components/ui";
import { isMock, mockSnapshot } from "../../lib/mock";
import { fmtNum, lang, msgs, useMsgs } from "../../i18n";

/** Anzeigename eines Vokabular-Eintrags in der aktuellen Sprache (CONTRACT I18N.C). */
export function vocabDisplayName(v: Pick<InventoryVocabEntry, "name_de" | "prompts" | "names">): string {
  const l = lang();
  return v.names?.[l] ?? (l === "de" ? v.name_de : v.names?.en ?? v.prompts[0] ?? v.name_de);
}

/** W2 (D2): Schwellen je Gegenstand (aus Rückfragen nachgeführt oder von Hand) – Änderungen gelten beim Speichern. */
function LabelThresholds({ f, set, cameraId, vocab }: {
  f: CameraInput; set: <K extends keyof CameraInput>(k: K, v: CameraInput[K]) => void; cameraId?: number;
  vocab: InventoryVocabEntry[];
}) {
  const th = useQuery({ queryKey: ["review", "thresholds", cameraId], enabled: cameraId !== undefined,
    queryFn: () => api.reviewThresholds(cameraId) });
  const names = new Map(vocab.map((v) => [v.label, v]));
  const scores = f.inventory_label_min_scores ?? {};
  const rows = new Map<string, { base: number; auto: boolean; yes: number; no: number }>();
  for (const r of th.data ?? []) rows.set(r.label, { base: r.base, auto: r.auto, yes: r.yes, no: r.no });
  for (const lb of Object.keys(scores)) {
    if (!rows.has(lb)) rows.set(lb, { base: names.get(lb)?.min_score ?? f.inventory_min_score, auto: false, yes: 0, no: 0 });
  }
  const setScore = (lb: string, v: number | null) => {
    const next = { ...scores };
    if (v === null) delete next[lb]; else next[lb] = Math.round(v * 1000) / 1000;
    set("inventory_label_min_scores", next);
  };
  const m = useMsgs().cameras.inv;
  const addable = vocab.filter((v) => !rows.has(v.label) && (f.inventory_labels.length === 0 || f.inventory_labels.includes(v.label)));
  return (
    <div className="field span-2">
      <label className="row" style={{ gap: 6 }}><SlidersHorizontal size={14} />{m.thresholds}</label>
      <span className="hint">
        {m.thresholdsHint}
      </span>
      {rows.size === 0 ? <div className="muted" style={{ fontSize: 13 }}>{m.noThresholds}</div> : (
        <table className="table" style={{ marginTop: 6 }}>
          <thead><tr><th>{m.colItem}</th><th>{m.colBase}</th><th>{m.colCurrent}</th><th>{m.colYesNo}</th><th></th></tr></thead>
          <tbody>
            {[...rows.entries()].map(([lb, r]) => {
              const cur = scores[lb];
              const manual = cur !== undefined && !r.auto;
              return (
                <tr key={lb}>
                  <td>{names.get(lb) ? vocabDisplayName(names.get(lb)!) : lb}</td>
                  <td className="mono">{fmtNum(r.base, 2)}</td>
                  <td>
                    <input className="input input-sm mono" type="number" min={0.05} max={0.95} step={0.01} style={{ width: 90 }}
                      value={cur ?? r.base} onChange={(e) => { const v = Number(e.target.value); if (v >= 0.05 && v <= 0.95) setScore(lb, v); }} />
                    <span className="muted" style={{ fontSize: 11, marginLeft: 6 }}>{cur === undefined ? m.base : manual ? m.manual : m.auto}</span>
                  </td>
                  <td className="mono">{r.yes} / {r.no}</td>
                  <td>{cur !== undefined && <button type="button" className="btn btn-ghost btn-sm" onClick={() => setScore(lb, null)}><RotateCcw size={13} />{m.reset}</button>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {addable.length > 0 && (
        <select className="select input-sm" style={{ width: 340, marginTop: 6 }} value="" onChange={(e) => e.target.value && setScore(e.target.value, f.inventory_min_score)}>
          <option value="">{m.addThreshold}</option>
          {addable.map((v) => <option key={v.label} value={v.label}>{vocabDisplayName(v)}</option>)}
        </select>
      )}
    </div>
  );
}

/** W2 (D2): Unterdrückte Stellen (Fehlerkennungen, mit „Nein“ beantwortet) mit Vorschau auf dem Kamerabild. */
function SuppressList({ f, set, cameraId, vocab }: {
  f: CameraInput; set: <K extends keyof CameraInput>(k: K, v: CameraInput[K]) => void; cameraId?: number;
  vocab: InventoryVocabEntry[];
}) {
  const list = f.inventory_suppress ?? [];
  const names = new Map(vocab.map((v) => [v.label, vocabDisplayName(v)]));
  const m = useMsgs().cameras.inv;
  const snap = cameraId === undefined ? null : isMock ? mockSnapshot(cameraId) : `/api/cameras/${cameraId}/snapshot.jpg`;
  const remove = (s: InventorySuppress) => set("inventory_suppress", list.filter((x) => x !== s));
  return (
    <div className="field span-2">
      <label className="row" style={{ gap: 6 }}><EyeOff size={14} />{m.suppressed(list.length)}</label>
      <span className="hint">{m.suppressedHint}</span>
      {list.length === 0 ? <div className="muted" style={{ fontSize: 13 }}>{m.none}</div> : (
        <div style={{ display: "grid", gridTemplateColumns: "minmax(200px, 360px) 1fr", gap: 12, alignItems: "start", marginTop: 6 }}>
          {snap ? (
            <div style={{ position: "relative", lineHeight: 0, borderRadius: 8, overflow: "hidden", background: "var(--bg-4)" }}>
              <img src={snap} alt={m.cameraImage} style={{ width: "100%", height: "auto", display: "block" }} />
              {list.map((s, i) => (
                <div key={i} title={names.get(s.label) ?? s.label} style={{
                  position: "absolute", left: `${s.bbox[0] * 100}%`, top: `${s.bbox[1] * 100}%`, width: `${(s.bbox[2] - s.bbox[0]) * 100}%`,
                  height: `${(s.bbox[3] - s.bbox[1]) * 100}%`, border: "2px dashed #f87171", borderRadius: 3,
                }} />
              ))}
            </div>
          ) : <div />}
          <table className="table">
            <tbody>
              {list.map((s, i) => (
                <tr key={i}>
                  <td>{names.get(s.label) ?? s.label}</td>
                  <td className="mono muted" style={{ fontSize: 11 }}>{s.bbox.map((v) => v.toFixed(2)).join(", ")}</td>
                  <td className="muted" style={{ fontSize: 12 }}>{s.review_id ? m.review(s.review_id) : m.manual}</td>
                  <td><button type="button" className="btn btn-ghost btn-sm" onClick={() => remove(s)}><Trash2 size={13} />{m.remove}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** Vokabular nach Anzeige-Gruppe sortiert (Reihenfolge des Vokabulars bleibt innerhalb der Gruppe erhalten). */
export function groupVocab(vocab: InventoryVocabEntry[]): [string, InventoryVocabEntry[]][] {
  const m = new Map<string, InventoryVocabEntry[]>();
  for (const v of vocab) {
    const g = v.group ? groupName(v.group) : msgs().cameras.inv.otherGroup;
    if (!m.has(g)) m.set(g, []);
    m.get(g)!.push(v);
  }
  return [...m.entries()];
}

/** Abschnitt „Gegenstandserkennung“ im Kamera-Dialog. */
export function CameraInventoryTab({ f, set, cameraId }: {
  f: CameraInput;
  set: <K extends keyof CameraInput>(k: K, v: CameraInput[K]) => void;
  cameraId?: number;
}) {
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const vocab = useMemo(() => settings.data?.inventory_vocabulary ?? [], [settings.data]);
  const groups = useMemo(() => groupVocab(vocab), [vocab]);
  const known = new Set(vocab.map((v) => v.label));
  const sel = f.inventory_labels;
  const unknown = sel.filter((l) => vocab.length > 0 && !known.has(l));
  const toggle = (label: string) => set("inventory_labels", sel.includes(label) ? sel.filter((l) => l !== label) : [...sel, label]);
  const setGroup = (entries: InventoryVocabEntry[], on: boolean) => {
    const labels = entries.map((e) => e.label);
    set("inventory_labels", on ? [...sel, ...labels.filter((l) => !sel.includes(l))] : sel.filter((l) => !labels.includes(l)));
  };
  const off = !f.inventory_enabled;
  const msg = useMsgs();
  const m = msg.cameras.inv;

  return (
    <div className="form-grid">
      <div className="field span-2">
        <label>{m.detection}</label>
        <div style={{ minHeight: 38, display: "flex", alignItems: "center" }}>
          <Switch checked={f.inventory_enabled} onChange={(v) => set("inventory_enabled", v)}
            label={f.inventory_enabled ? m.detectionOn : msg.common.off} />
        </div>
        <span className="hint">
          {m.detectionHint}
        </span>
      </div>
      <div className="field"><label>{m.room}</label>
        <input className="input" value={f.room ?? ""} maxLength={100} placeholder={m.roomPlaceholder}
          onChange={(e) => set("room", e.target.value || null)} />
        <span className="hint">{m.roomHint}</span>
      </div>
      <div className="field"><label>{m.interval}</label>
        <Range value={f.inventory_interval_s} min={1} max={60} step={1} onChange={(v) => set("inventory_interval_s", v)}
          format={msg.cameras.every} />
        <span className="hint">{m.intervalHint}</span>
      </div>
      <div className="field"><label>{m.minScore}</label>
        <Range value={f.inventory_min_score} min={0.05} max={0.95} step={0.05} onChange={(v) => set("inventory_min_score", v)}
          format={(v) => `${Math.round(v * 100)} %`} />
        <span className="hint">{m.minScoreHint}</span>
      </div>
      <div className="field"><label>{m.tiling}</label>
        <div style={{ minHeight: 38, display: "flex", alignItems: "center" }}>
          <Switch checked={f.inventory_tiling} onChange={(v) => set("inventory_tiling", v)}
            label={f.inventory_tiling ? m.tilingOn : m.tilingOff} />
        </div>
        <span className="hint">{m.tilingHint}</span>
      </div>

      <div className="field span-2" style={{ opacity: off ? 0.6 : 1 }}>
        <div className="row">
          <label className="label" style={{ flex: 1 }}>
            {m.items}{" "}
            <span className="muted">
              {sel.length === 0 ? m.allOf(vocab.length) : m.selectedOf(sel.length, vocab.length)}
            </span>
          </label>
          {sel.length > 0 && <button type="button" className="btn btn-ghost btn-sm" onClick={() => set("inventory_labels", [])}>{m.clearSel}</button>}
        </div>
        <span className="hint">{m.selHint}</span>
        {settings.isLoading ? <div className="muted">{m.loadingVocab}</div> : vocab.length === 0 ? (
          <div className="muted">{m.vocabEmpty}</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 6 }}>
            {groups.map(([g, entries]) => {
              const allOn = entries.every((e) => sel.includes(e.label));
              return (
                <div key={g}>
                  <div className="row" style={{ marginBottom: 6 }}>
                    <span className="section-title" style={{ margin: 0 }}>{g}</span>
                    <button type="button" className="btn btn-ghost btn-sm" style={{ height: 22, fontSize: 11 }} onClick={() => setGroup(entries, !allOn)}>
                      {allOn ? m.groupOff : m.groupOn}
                    </button>
                  </div>
                  <div className="chips">
                    {entries.map((e) => {
                      const on = sel.includes(e.label);
                      return (
                        <button type="button" key={e.label} className={`chip ${on ? "on" : ""}`} title={e.prompts.join(", ")} onClick={() => toggle(e.label)}>
                          <Package size={12} />{vocabDisplayName(e)}{on && <Check size={13} />}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )}
        {unknown.length > 0 && (
          <div className="test-result bad" style={{ marginTop: 8 }}>
            {m.notInVocab(unknown.join(", "))}
            <button type="button" className="btn btn-sm" style={{ marginLeft: "auto" }}
              onClick={() => set("inventory_labels", sel.filter((l) => known.has(l)))}>{m.remove}</button>
          </div>
        )}
      </div>
      <LabelThresholds f={f} set={set} cameraId={cameraId} vocab={vocab} />
      <SuppressList f={f} set={set} cameraId={cameraId} vocab={vocab} />
    </div>
  );
}
