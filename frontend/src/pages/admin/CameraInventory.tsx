import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, EyeOff, Package, RotateCcw, SlidersHorizontal, Trash2 } from "lucide-react";
import { api } from "../../lib/api";
import type { CameraInput, InventorySuppress, InventoryVocabEntry } from "../../lib/types";
import { Range, Switch } from "../../components/ui";
import { isMock, mockSnapshot } from "../../lib/mock";

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
  const addable = vocab.filter((v) => !rows.has(v.label) && (f.inventory_labels.length === 0 || f.inventory_labels.includes(v.label)));
  return (
    <div className="field span-2">
      <label className="row" style={{ gap: 6 }}><SlidersHorizontal size={14} />Schwellen je Gegenstand</label>
      <span className="hint">
        Antworten unter „Bitte bestätigen“ verschieben die Schwelle automatisch (höchstens 0,05 je Antwort, ab 3 Antworten).
        Ein hier von Hand geänderter Wert hat Vorrang und wird nicht mehr nachgeführt; „Zurücksetzen“ gibt ihn wieder frei.
      </span>
      {rows.size === 0 ? <div className="muted" style={{ fontSize: 13 }}>Noch keine eigenen Schwellen und keine Antworten.</div> : (
        <table className="table" style={{ marginTop: 6 }}>
          <thead><tr><th>Gegenstand</th><th>Basis</th><th>aktuell</th><th>Ja / Nein</th><th></th></tr></thead>
          <tbody>
            {[...rows.entries()].map(([lb, r]) => {
              const cur = scores[lb];
              const manual = cur !== undefined && !r.auto;
              return (
                <tr key={lb}>
                  <td>{names.get(lb)?.name_de ?? lb}</td>
                  <td className="mono">{r.base.toFixed(2)}</td>
                  <td>
                    <input className="input input-sm mono" type="number" min={0.05} max={0.95} step={0.01} style={{ width: 90 }}
                      value={cur ?? r.base} onChange={(e) => { const v = Number(e.target.value); if (v >= 0.05 && v <= 0.95) setScore(lb, v); }} />
                    <span className="muted" style={{ fontSize: 11, marginLeft: 6 }}>{cur === undefined ? "Basis" : manual ? "von Hand" : "automatisch"}</span>
                  </td>
                  <td className="mono">{r.yes} / {r.no}</td>
                  <td>{cur !== undefined && <button type="button" className="btn btn-ghost btn-sm" onClick={() => setScore(lb, null)}><RotateCcw size={13} />Zurücksetzen</button>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {addable.length > 0 && (
        <select className="select input-sm" style={{ width: 340, marginTop: 6 }} value="" onChange={(e) => e.target.value && setScore(e.target.value, f.inventory_min_score)}>
          <option value="">+ Schwelle für einen Gegenstand festlegen …</option>
          {addable.map((v) => <option key={v.label} value={v.label}>{v.name_de}</option>)}
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
  const names = new Map(vocab.map((v) => [v.label, v.name_de]));
  const snap = cameraId === undefined ? null : isMock ? mockSnapshot(cameraId) : `/api/cameras/${cameraId}/snapshot.jpg`;
  const remove = (s: InventorySuppress) => set("inventory_suppress", list.filter((x) => x !== s));
  return (
    <div className="field span-2">
      <label className="row" style={{ gap: 6 }}><EyeOff size={14} />Unterdrückte Stellen ({list.length})</label>
      <span className="hint">An diesen Stellen meldet die Erkennung den jeweiligen Gegenstand nicht mehr (gleiche Art, starke Überlappung). Entfernen, falls dort später wirklich so etwas liegt.</span>
      {list.length === 0 ? <div className="muted" style={{ fontSize: 13 }}>Keine.</div> : (
        <div style={{ display: "grid", gridTemplateColumns: "minmax(200px, 360px) 1fr", gap: 12, alignItems: "start", marginTop: 6 }}>
          {snap ? (
            <div style={{ position: "relative", lineHeight: 0, borderRadius: 8, overflow: "hidden", background: "var(--bg-4)" }}>
              <img src={snap} alt="Kamerabild" style={{ width: "100%", height: "auto", display: "block" }} />
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
                  <td className="muted" style={{ fontSize: 12 }}>{s.review_id ? `Rückfrage #${s.review_id}` : "von Hand"}</td>
                  <td><button type="button" className="btn btn-ghost btn-sm" onClick={() => remove(s)}><Trash2 size={13} />Entfernen</button></td>
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
    const g = v.group || "Sonstiges";
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

  return (
    <div className="form-grid">
      <div className="field span-2">
        <label>Gegenstandserkennung</label>
        <div style={{ minHeight: 38, display: "flex", alignItems: "center" }}>
          <Switch checked={f.inventory_enabled} onChange={(v) => set("inventory_enabled", v)}
            label={f.inventory_enabled ? "aktiv – Gegenstände im Bild werden regelmäßig erfasst" : "aus"} />
        </div>
        <span className="hint">
          Erkennt ruhende Gegenstände (z. B. Schlüssel, Fernbedienung, Buch) in festen Abständen und merkt sich, wo sie
          zuletzt gesehen wurden. Läuft zusätzlich zur Objekterkennung und benötigt etwas GPU-Speicher.
        </span>
      </div>
      <div className="field"><label>Raum</label>
        <input className="input" value={f.room ?? ""} maxLength={100} placeholder="z. B. Wohnzimmer"
          onChange={(e) => set("room", e.target.value || null)} />
        <span className="hint">Gruppiert Gegenstände auf der Seite „Gegenstände“ und in der Suche.</span>
      </div>
      <div className="field"><label>Intervall</label>
        <Range value={f.inventory_interval_s} min={1} max={60} step={1} onChange={(v) => set("inventory_interval_s", v)}
          format={(v) => `alle ${v} s`} />
        <span className="hint">Abstand zwischen zwei Durchläufen. Größer = weniger GPU-Last.</span>
      </div>
      <div className="field"><label>Mindest-Konfidenz</label>
        <Range value={f.inventory_min_score} min={0.05} max={0.95} step={0.05} onChange={(v) => set("inventory_min_score", v)}
          format={(v) => `${Math.round(v * 100)} %`} />
        <span className="hint">Gilt für alle Gegenstände ohne eigene Schwelle im Vokabular.</span>
      </div>
      <div className="field"><label>Kachelung</label>
        <div style={{ minHeight: 38, display: "flex", alignItems: "center" }}>
          <Switch checked={f.inventory_tiling} onChange={(v) => set("inventory_tiling", v)}
            label={f.inventory_tiling ? "Bild in Kacheln analysieren" : "ganzes Bild auf einmal"} />
        </div>
        <span className="hint">Findet kleine Gegenstände in hochauflösenden Bildern besser, kostet aber mehr Rechenzeit.</span>
      </div>

      <div className="field span-2" style={{ opacity: off ? 0.6 : 1 }}>
        <div className="row">
          <label className="label" style={{ flex: 1 }}>
            Gegenstände{" "}
            <span className="muted">
              {sel.length === 0 ? `– alle ${vocab.length} aus dem Vokabular` : `– ${sel.length} von ${vocab.length} ausgewählt`}
            </span>
          </label>
          {sel.length > 0 && <button type="button" className="btn btn-ghost btn-sm" onClick={() => set("inventory_labels", [])}>Alle (Auswahl leeren)</button>}
        </div>
        <span className="hint">Keine Auswahl = gesamtes Vokabular (Administration → Einstellungen). Weniger Gegenstände = schneller und weniger Fehlerkennungen.</span>
        {settings.isLoading ? <div className="muted">Lade Vokabular …</div> : vocab.length === 0 ? (
          <div className="muted">Das Vokabular ist leer – unter Einstellungen Gegenstände anlegen.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 6 }}>
            {groups.map(([g, entries]) => {
              const allOn = entries.every((e) => sel.includes(e.label));
              return (
                <div key={g}>
                  <div className="row" style={{ marginBottom: 6 }}>
                    <span className="section-title" style={{ margin: 0 }}>{g}</span>
                    <button type="button" className="btn btn-ghost btn-sm" style={{ height: 22, fontSize: 11 }} onClick={() => setGroup(entries, !allOn)}>
                      {allOn ? "Gruppe abwählen" : "Gruppe wählen"}
                    </button>
                  </div>
                  <div className="chips">
                    {entries.map((e) => {
                      const on = sel.includes(e.label);
                      return (
                        <button type="button" key={e.label} className={`chip ${on ? "on" : ""}`} title={e.prompts.join(", ")} onClick={() => toggle(e.label)}>
                          <Package size={12} />{e.name_de}{on && <Check size={13} />}
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
            Nicht mehr im Vokabular: {unknown.join(", ")}
            <button type="button" className="btn btn-sm" style={{ marginLeft: "auto" }}
              onClick={() => set("inventory_labels", sel.filter((l) => known.has(l)))}>Entfernen</button>
          </div>
        )}
      </div>
      <LabelThresholds f={f} set={set} cameraId={cameraId} vocab={vocab} />
      <SuppressList f={f} set={set} cameraId={cameraId} vocab={vocab} />
    </div>
  );
}
