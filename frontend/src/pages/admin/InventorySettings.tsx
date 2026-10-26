import { groupName } from "../../lib/labels";
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Package, Plus, Search, Trash2 } from "lucide-react";
import type { InventoryVocabEntry, Settings } from "../../lib/types";
import { Range } from "../../components/ui";
import { msgs, useMsgs } from "../../i18n";

const LABEL_RE = /^[a-z0-9_]{1,40}$/;

const splitList = (t: string) => t.split(",").map((x) => x.trim()).filter(Boolean);

/** Kommagetrennte Liste; hält den Rohtext lokal, damit Kommas/Leerzeichen beim Tippen nicht verschwinden. */
function ListInput({ value, onChange, placeholder, mono }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string; mono?: boolean }) {
  const [text, setText] = useState(value.join(", "));
  useEffect(() => {
    if (splitList(text).join("\u0000") !== value.join("\u0000")) setText(value.join(", "));
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <input className={`input input-sm ${mono ? "mono" : ""}`} value={text} placeholder={placeholder}
      onChange={(e) => { setText(e.target.value); onChange(splitList(e.target.value)); }} />
  );
}

const slug = (t: string) => t.toLowerCase().trim().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);

/** Prüft das Vokabular wie das Backend (CONTRACT.md, Teil F); liefert Fehlertexte je Index. */
export function vocabErrors(vocab: InventoryVocabEntry[]): Record<number, string[]> {
  const m = msgs().settings.inventory;
  const out: Record<number, string[]> = {};
  const seen = new Map<string, number>();
  vocab.forEach((v, i) => {
    const e: string[] = [];
    if (!LABEL_RE.test(v.label)) e.push(m.errKey);
    else if (seen.has(v.label)) e.push(m.errKeyDup(v.label));
    seen.set(v.label, i);
    if (!v.name_de.trim() || v.name_de.length > 60) e.push(m.errName);
    if (v.prompts.length < 1 || v.prompts.length > 8) e.push(m.errPrompts);
    if (v.prompts.some((p) => p.length > 60)) e.push(m.errPromptLen);
    if (v.min_score !== null && (v.min_score < 0.05 || v.min_score > 0.95)) e.push(m.errScore);
    if (e.length) out[i] = e;
  });
  return out;
}

/** Karte „Gegenstände (Inventar)“ für Administration → Einstellungen. */
export function InventorySettingsCard({ f, saved, patch }: { f: Settings; saved?: Settings; patch: (p: Partial<Settings>) => void }) {
  const all = useMsgs();
  const m = all.settings.inventory;
  const vocab = f.inventory_vocabulary ?? [];
  const synonyms = f.inventory_synonyms ?? {};
  const savedLabels = useMemo(() => new Set((saved?.inventory_vocabulary ?? []).map((v) => v.label)), [saved]);
  const [filter, setFilter] = useState("");
  const errors = useMemo(() => vocabErrors(vocab), [vocab, m]);
  const errCount = Object.keys(errors).length;
  const groupNames = useMemo(() => [...new Set(vocab.map((v) => v.group).filter(Boolean))], [vocab]);

  const updateEntry = (i: number, p: Partial<InventoryVocabEntry>) => {
    const old = vocab[i];
    const next = { ...old, ...p };
    // Neuer Eintrag: Schlüssel automatisch aus dem ersten Prompt ableiten, solange er nicht von Hand gesetzt wurde
    if (!savedLabels.has(old.label) && p.prompts && (old.label === "" || old.label === slug(old.prompts[0] ?? ""))) {
      next.label = slug(p.prompts[0] ?? "");
    }
    const nv = vocab.slice();
    nv[i] = next;
    const out: Partial<Settings> = { inventory_vocabulary: nv };
    if (next.label !== old.label && synonyms[old.label]) {
      const ns = { ...synonyms, [next.label]: synonyms[old.label] };
      delete ns[old.label];
      out.inventory_synonyms = ns;
    }
    patch(out);
  };
  const setSyn = (label: string, list: string[]) => {
    const ns = { ...synonyms };
    if (list.length) ns[label] = list; else delete ns[label];
    patch({ inventory_synonyms: ns });
  };
  const remove = (i: number) => {
    const label = vocab[i].label;
    const ns = { ...synonyms };
    if (!vocab.some((v, k) => k !== i && v.label === label)) delete ns[label];
    patch({ inventory_vocabulary: vocab.filter((_, k) => k !== i), inventory_synonyms: ns });
  };
  const add = () => {
    setFilter("");
    patch({ inventory_vocabulary: [...vocab, { label: "", name_de: "", group: m.defaultGroup, prompts: [], min_score: null }] });
  };

  // Gruppiert anzeigen, Reihenfolge des Vokabulars (= Reihenfolge im Worker) bleibt erhalten
  const fq = filter.trim().toLowerCase();
  const groups = useMemo(() => {
    const byGroup = new Map<string, number[]>();
    vocab.forEach((v, i) => {
      if (fq && ![v.label, v.name_de, v.group, ...v.prompts, ...(synonyms[v.label] ?? [])].some((x) => x.toLowerCase().includes(fq))) return;
      const g = v.group || m.noGroup;
      if (!byGroup.has(g)) byGroup.set(g, []);
      byGroup.get(g)!.push(i);
    });
    return [...byGroup.entries()];
  }, [vocab, synonyms, fq, m]);

  return (
    <div className="card">
      <div className="card-head"><Package size={18} color="var(--accent)" /><h3>{m.title}</h3></div>
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        <div className="form-grid">
          <div className="field"><label>{m.confirmN}</label>
            <Range value={f.inventory_confirm_n} min={1} max={10} step={1} onChange={(v) => patch({ inventory_confirm_n: v })} format={(v) => `${v}×`} />
            <span className="hint">{m.confirmNHint}</span>
          </div>
          <div className="field"><label>{m.missM}</label>
            <Range value={f.inventory_miss_m} min={1} max={20} step={1} onChange={(v) => patch({ inventory_miss_m: v })} format={(v) => `${v}×`} />
            <span className="hint">{m.missMHint}</span>
          </div>
          <div className="field"><label>{m.retention}</label>
            <div className="row"><input type="number" min={1} max={3650} className="input" value={f.retention_days_inventory}
              onChange={(e) => patch({ retention_days_inventory: Number(e.target.value) })} /><span className="muted">{all.settings.shared.daysUnit}</span></div>
            <span className="hint">{m.retentionHint}</span>
          </div>
          <div className="field"><label>{m.changeMin}</label>
            <div className="row"><input type="number" min={0} max={3600} className="input" value={f.inventory_change_min_s}
              onChange={(e) => patch({ inventory_change_min_s: Number(e.target.value) })} /><span className="muted">{all.settings.shared.secondsUnit}</span></div>
            <span className="hint">{m.changeMinHint}</span>
          </div>
        </div>

        <div>
          <div className="row-wrap" style={{ marginBottom: 10 }}>
            <div style={{ flex: 1, minWidth: 200 }}>
              <div style={{ fontWeight: 600 }}>{m.vocabulary} <span className="muted" style={{ fontWeight: 400 }}>· {m.vocabCount(vocab.length)}</span></div>
              <div className="muted" style={{ fontSize: 12 }}>
                {m.vocabHint}
              </div>
            </div>
            <div className="input-group" style={{ width: 220 }}>
              <Search size={15} />
              <input className="input input-sm" value={filter} placeholder={m.filterPlaceholder} onChange={(e) => setFilter(e.target.value)} />
            </div>
            <button type="button" className="btn btn-sm" onClick={add}><Plus size={14} />{m.add}</button>
          </div>
          {errCount > 0 && (
            <div className="test-result bad" style={{ marginBottom: 10 }}>
              <AlertTriangle size={16} />{m.incomplete(errCount)}
            </div>
          )}
          <div className="card" style={{ overflow: "auto", maxHeight: 620 }}>
            <table className="table" style={{ minWidth: 1260 }}>
              <thead>
                <tr><th style={{ width: 160 }}>{m.colName}</th><th style={{ width: 160 }}>{m.colNameEn}</th><th style={{ width: 130 }}>{m.colKey}</th><th style={{ width: 180 }}>{m.colGroup}</th>
                  <th>{m.colPrompts}</th><th>{m.colSynonyms}</th><th style={{ width: 110 }}>{m.colScore}</th><th style={{ width: 44 }}></th></tr>
              </thead>
              <tbody>
                {groups.length === 0 && <tr><td colSpan={8} className="muted">{m.noEntries(!!fq)}</td></tr>}
                {groups.map(([g, idx]) => [
                  <tr key={`g-${g}`}><td colSpan={8} className="section-title" style={{ padding: "12px 14px 6px", margin: 0, background: "var(--bg-1)" }}>{groupName(g)} · {idx.length}</td></tr>,
                  ...idx.map((i) => {
                    const v = vocab[i];
                    const isNew = !savedLabels.has(v.label);
                    const err = errors[i];
                    return (
                      <tr key={`e-${i}`} style={err ? { background: "rgba(239,68,68,.06)" } : undefined} title={err?.join(" · ")}>
                        <td><input className="input input-sm" value={v.name_de} placeholder={m.namePlaceholder} maxLength={60}
                          onChange={(e) => updateEntry(i, { name_de: e.target.value })} autoFocus={isNew && !v.name_de && i === vocab.length - 1} /></td>
                        <td><input className="input input-sm" value={v.names?.en ?? ""} placeholder={m.nameEnPlaceholder} maxLength={60}
                          onChange={(e) => updateEntry(i, { names: { ...(v.names ?? {}), en: e.target.value } })} /></td>
                        <td>{isNew ? (
                          <input className="input input-sm mono" value={v.label} placeholder="keys" title={m.keyTitle}
                            onChange={(e) => updateEntry(i, { label: e.target.value.toLowerCase() })} />
                        ) : <span className="mono dim" title={m.keyFixedTitle}>{v.label}</span>}</td>
                        <td><input className="input input-sm" value={v.group} list="vg-inv-groups" placeholder={m.groupPlaceholder}
                          onChange={(e) => updateEntry(i, { group: e.target.value })} /></td>
                        <td><ListInput value={v.prompts} placeholder="keys, key ring" mono onChange={(p) => updateEntry(i, { prompts: p })} /></td>
                        <td><ListInput value={synonyms[v.label] ?? []} placeholder={m.synonymPlaceholder} onChange={(s) => setSyn(v.label, s)} /></td>
                        <td><input type="number" className="input input-sm" min={5} max={95} step={5} placeholder={m.scorePlaceholder}
                          title={m.scoreTitle}
                          value={v.min_score === null ? "" : Math.round(v.min_score * 100)}
                          onChange={(e) => updateEntry(i, { min_score: e.target.value === "" ? null : Number(e.target.value) / 100 })} /></td>
                        <td><button type="button" className="btn btn-ghost btn-icon btn-sm" title={all.common.remove} onClick={() => remove(i)}><Trash2 size={15} /></button></td>
                      </tr>
                    );
                  }),
                ])}
              </tbody>
            </table>
            <datalist id="vg-inv-groups">{groupNames.map((g) => <option key={g} value={g} />)}</datalist>
          </div>
          <div className="hint muted" style={{ fontSize: 12, marginTop: 8 }}>
            {m.footer}
          </div>
        </div>
      </div>
    </div>
  );
}
