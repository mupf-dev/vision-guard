import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Package, Plus, Search, Trash2 } from "lucide-react";
import type { InventoryVocabEntry, Settings } from "../../lib/types";
import { Range } from "../../components/ui";

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
  const out: Record<number, string[]> = {};
  const seen = new Map<string, number>();
  vocab.forEach((v, i) => {
    const e: string[] = [];
    if (!LABEL_RE.test(v.label)) e.push("Schlüssel: nur a–z, 0–9 und _ (1–40 Zeichen)");
    else if (seen.has(v.label)) e.push(`Schlüssel „${v.label}“ doppelt`);
    seen.set(v.label, i);
    if (!v.name_de.trim() || v.name_de.length > 60) e.push("Name: 1–60 Zeichen");
    if (v.prompts.length < 1 || v.prompts.length > 8) e.push("1–8 Prompts");
    if (v.prompts.some((p) => p.length > 60)) e.push("Prompt länger als 60 Zeichen");
    if (v.min_score !== null && (v.min_score < 0.05 || v.min_score > 0.95)) e.push("Konfidenz 5–95 %");
    if (e.length) out[i] = e;
  });
  return out;
}

/** Karte „Gegenstände (Inventar)“ für Administration → Einstellungen. */
export function InventorySettingsCard({ f, saved, patch }: { f: Settings; saved?: Settings; patch: (p: Partial<Settings>) => void }) {
  const vocab = f.inventory_vocabulary ?? [];
  const synonyms = f.inventory_synonyms ?? {};
  const savedLabels = useMemo(() => new Set((saved?.inventory_vocabulary ?? []).map((v) => v.label)), [saved]);
  const [filter, setFilter] = useState("");
  const errors = useMemo(() => vocabErrors(vocab), [vocab]);
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
    patch({ inventory_vocabulary: [...vocab, { label: "", name_de: "", group: "Sonstiges", prompts: [], min_score: null }] });
  };

  // Gruppiert anzeigen, Reihenfolge des Vokabulars (= Reihenfolge im Worker) bleibt erhalten
  const fq = filter.trim().toLowerCase();
  const groups = useMemo(() => {
    const m = new Map<string, number[]>();
    vocab.forEach((v, i) => {
      if (fq && ![v.label, v.name_de, v.group, ...v.prompts, ...(synonyms[v.label] ?? [])].some((x) => x.toLowerCase().includes(fq))) return;
      const g = v.group || "Ohne Gruppe";
      if (!m.has(g)) m.set(g, []);
      m.get(g)!.push(i);
    });
    return [...m.entries()];
  }, [vocab, synonyms, fq]);

  return (
    <div className="card">
      <div className="card-head"><Package size={18} color="var(--accent)" /><h3>Gegenstände (Inventar)</h3></div>
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        <div className="form-grid">
          <div className="field"><label>Bestätigung nach Treffern</label>
            <Range value={f.inventory_confirm_n} min={1} max={10} step={1} onChange={(v) => patch({ inventory_confirm_n: v })} format={(v) => `${v}×`} />
            <span className="hint">So oft muss ein Gegenstand erkannt werden, bevor er als „sichtbar“ gilt.</span>
          </div>
          <div className="field"><label>„Nicht mehr gesehen“ nach Fehlversuchen</label>
            <Range value={f.inventory_miss_m} min={1} max={20} step={1} onChange={(v) => patch({ inventory_miss_m: v })} format={(v) => `${v}×`} />
            <span className="hint">Durchläufe in Folge ohne Treffer. Verdeckung durch Personen zählt nicht.</span>
          </div>
          <div className="field"><label>Verlauf der Gegenstände aufbewahren</label>
            <div className="row"><input type="number" min={1} max={3650} className="input" value={f.retention_days_inventory}
              onChange={(e) => patch({ retention_days_inventory: Number(e.target.value) })} /><span className="muted">Tage</span></div>
            <span className="hint">Ältere Verlaufseinträge und nicht mehr gesehene Gegenstände werden gelöscht.</span>
          </div>
          <div className="field"><label>Veränderungen: Mindestdauer</label>
            <div className="row"><input type="number" min={0} max={3600} className="input" value={f.inventory_change_min_s}
              onChange={(e) => patch({ inventory_change_min_s: Number(e.target.value) })} /><span className="muted">Sekunden</span></div>
            <span className="hint">Kürzer sichtbare Gegenstände zählen unter „Veränderungen“ nicht als neu.</span>
          </div>
        </div>

        <div>
          <div className="row-wrap" style={{ marginBottom: 10 }}>
            <div style={{ flex: 1, minWidth: 200 }}>
              <div style={{ fontWeight: 600 }}>Vokabular <span className="muted" style={{ fontWeight: 400 }}>· {vocab.length} Gegenstände</span></div>
              <div className="muted" style={{ fontSize: 12 }}>
                Deutscher Name für Anzeige und Suche, englische Prompts für das Erkennungsmodell, Synonyme als zusätzliche
                deutsche Suchwörter (kommagetrennt). Personen und Fahrzeuge gehören nicht hierher.
              </div>
            </div>
            <div className="input-group" style={{ width: 220 }}>
              <Search size={15} />
              <input className="input input-sm" value={filter} placeholder="Vokabular filtern …" onChange={(e) => setFilter(e.target.value)} />
            </div>
            <button type="button" className="btn btn-sm" onClick={add}><Plus size={14} />Gegenstand hinzufügen</button>
          </div>
          {errCount > 0 && (
            <div className="test-result bad" style={{ marginBottom: 10 }}>
              <AlertTriangle size={16} />{errCount} {errCount === 1 ? "Eintrag ist" : "Einträge sind"} unvollständig – Speichern wird vom Server abgelehnt.
            </div>
          )}
          <div className="card" style={{ overflow: "auto", maxHeight: 620 }}>
            <table className="table" style={{ minWidth: 1100 }}>
              <thead>
                <tr><th style={{ width: 170 }}>Name (deutsch)</th><th style={{ width: 130 }}>Schlüssel</th><th style={{ width: 180 }}>Gruppe</th>
                  <th>Prompts (englisch)</th><th>Synonyme (Suche)</th><th style={{ width: 110 }}>Konfidenz</th><th style={{ width: 44 }}></th></tr>
              </thead>
              <tbody>
                {groups.length === 0 && <tr><td colSpan={7} className="muted">Keine Einträge{fq ? " für diesen Filter" : ""}.</td></tr>}
                {groups.map(([g, idx]) => [
                  <tr key={`g-${g}`}><td colSpan={7} className="section-title" style={{ padding: "12px 14px 6px", margin: 0, background: "var(--bg-1)" }}>{g} · {idx.length}</td></tr>,
                  ...idx.map((i) => {
                    const v = vocab[i];
                    const isNew = !savedLabels.has(v.label);
                    const err = errors[i];
                    return (
                      <tr key={`e-${i}`} style={err ? { background: "rgba(239,68,68,.06)" } : undefined} title={err?.join(" · ")}>
                        <td><input className="input input-sm" value={v.name_de} placeholder="z. B. Schlüssel" maxLength={60}
                          onChange={(e) => updateEntry(i, { name_de: e.target.value })} autoFocus={isNew && !v.name_de && i === vocab.length - 1} /></td>
                        <td>{isNew ? (
                          <input className="input input-sm mono" value={v.label} placeholder="keys" title="Stabiler englischer Schlüssel (a–z, 0–9, _)"
                            onChange={(e) => updateEntry(i, { label: e.target.value.toLowerCase() })} />
                        ) : <span className="mono dim" title="Schlüssel bestehender Einträge ist fest (Bezug zu erkannten Gegenständen)">{v.label}</span>}</td>
                        <td><input className="input input-sm" value={v.group} list="vg-inv-groups" placeholder="Gruppe"
                          onChange={(e) => updateEntry(i, { group: e.target.value })} /></td>
                        <td><ListInput value={v.prompts} placeholder="keys, key ring" mono onChange={(p) => updateEntry(i, { prompts: p })} /></td>
                        <td><ListInput value={synonyms[v.label] ?? []} placeholder="Schlüsselbund, …" onChange={(s) => setSyn(v.label, s)} /></td>
                        <td><input type="number" className="input input-sm" min={5} max={95} step={5} placeholder="Kamera"
                          title="Eigene Mindest-Konfidenz in % – leer = Einstellung der Kamera"
                          value={v.min_score === null ? "" : Math.round(v.min_score * 100)}
                          onChange={(e) => updateEntry(i, { min_score: e.target.value === "" ? null : Number(e.target.value) / 100 })} /></td>
                        <td><button type="button" className="btn btn-ghost btn-icon btn-sm" title="Entfernen" onClick={() => remove(i)}><Trash2 size={15} /></button></td>
                      </tr>
                    );
                  }),
                ])}
              </tbody>
            </table>
            <datalist id="vg-inv-groups">{groupNames.map((g) => <option key={g} value={g} />)}</datalist>
          </div>
          <div className="hint muted" style={{ fontSize: 12, marginTop: 8 }}>
            Änderungen am Vokabular wirken nach dem Speichern auf alle Kameras mit Gegenstandserkennung. Entfernte Einträge
            sollten auch aus der Auswahl einzelner Kameras genommen werden.
          </div>
        </div>
      </div>
    </div>
  );
}
