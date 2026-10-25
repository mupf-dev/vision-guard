import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Moon, Play, ScanFace, User } from "lucide-react";
import { api } from "../../lib/api";
import type { NightlyAction, NightlyActionType, NightlyReport, NightlyReportSummary, Settings } from "../../lib/types";
import { fmtDateTime } from "../../lib/format";
import { ConfirmDialog, Dialog, Img, Loading, Range, Switch } from "../../components/ui";
import { toast } from "../../lib/store";

type Patch = (p: Partial<Settings>) => void;

/** Einstellungen → Gesichtserkennung (Identitäten 07, Pakete B/C; CONTRACT ID.E). */
export function FaceSettingsCard({ f, patch }: { f: Settings; patch: Patch }) {
  return (
    <div className="card">
      <div className="card-head"><ScanFace size={18} color="var(--accent)" /><h3>Gesichtserkennung</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>Neue Person erst ab Qualität</label>
          <Range value={f.face_new_min_quality} min={0} max={1} step={0.01} onChange={(v) => patch({ face_new_min_quality: v })} format={(v) => v.toFixed(2)} />
          <span className="hint">Schwächere Bilder dürfen nur bekannten Personen zugeordnet werden – weniger Einzelbild-Fragmente.</span>
        </div>
        <div className="field"><label>… und Gesichtsgröße ab</label>
          <div className="row"><input type="number" min={0} max={1000} className="input" value={f.face_new_min_size} onChange={(e) => patch({ face_new_min_size: Number(e.target.value) })} /><span className="muted">px</span></div>
        </div>
        <div className="field"><label>Beispielbild speichern ab Qualität</label>
          <Range value={f.face_sample_min_quality} min={0} max={1} step={0.01} onChange={(v) => patch({ face_sample_min_quality: v })} format={(v) => v.toFixed(2)} />
        </div>
        <div className="field"><label>Mindestabstand zum Zweitbesten</label>
          <Range value={f.face_match_margin} min={0} max={0.3} step={0.01} onChange={(v) => patch({ face_match_margin: v })} format={(v) => v.toFixed(2)} />
          <span className="hint">Liegen zwei Personen näher beieinander, wird nichts zugeordnet (ggf. Rückfrage).</span>
        </div>
        <div className="field"><label>Bilder je benannter Person</label>
          <div className="row"><input type="number" min={20} max={100} className="input" value={f.face_max_samples_named} onChange={(e) => patch({ face_max_samples_named: Number(e.target.value) })} /><span className="muted">unbenannt: 20</span></div>
        </div>
        <div className="field"><label>Beste Gesichter je Track</label>
          <div className="row"><input type="number" min={1} max={10} className="input" value={f.face_track_faces} onChange={(e) => patch({ face_track_faces: Number(e.target.value) })} /></div>
        </div>
        <div className="field span-2"><label>Gesichter aus voller Auflösung</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.face_fullres} onChange={(v) => patch({ face_fullres: v })} label={f.face_fullres ? "an" : "aus"} />
          </div>
          <span className="hint">Schneidet Gesichter aus dem Hauptstream, solange eine Person im Bild ist (mehr CPU). Je Kamera im Kamera-Dialog abschaltbar.</span>
        </div>
      </div>
    </div>
  );
}

const NIGHTLY_KEYS = ["nightly_mode", "nightly_time", "nightly_auto_merge_min", "nightly_auto_merge_margin", "nightly_review_min",
  "nightly_review_max_per_day", "nightly_curate", "nightly_thresholds", "nightly_cleanup_days"] as const;

const MODE_HINT: Record<Settings["nightly_mode"], string> = {
  off: "Kein automatischer Lauf. „Probelauf jetzt“ geht trotzdem.",
  preview: "Probelauf: erstellt jede Nacht nur einen Bericht, ändert nichts. Sichere Fälle lassen sich danach gezielt anwenden.",
  apply: "Führt sichere Fragmente automatisch zusammen (nur Unbenannte, nie zwei Benannte), stellt Rückfragen, kuratiert Galerien. Jede Zusammenführung lässt sich im Verlauf zurücksetzen.",
};

/** Einstellungen → Nachtlauf (Identitäten 07, Paket D; CONTRACT ID.H). */
export function NightlySettingsCard({ f, saved, patch }: { f: Settings; saved?: Settings; patch: Patch }) {
  const qc = useQueryClient();
  const st = useQuery({ queryKey: ["nightly", "status"], queryFn: api.nightlyStatus,
    refetchInterval: (q) => (q.state.data?.running ? 2000 : false) });
  const list = useQuery({ queryKey: ["nightly", "reports"], queryFn: () => api.nightlyReports({ limit: 8 }) });
  const [open, setOpen] = useState<number | null>(null);
  const dirty = saved !== undefined && NIGHTLY_KEYS.some((k) => JSON.stringify(f[k]) !== JSON.stringify(saved[k]));
  const running = !!st.data?.running;
  const run = useMutation({
    mutationFn: () => api.nightlyRun("preview"),
    onSuccess: (r) => { toast.success("Probelauf gestartet", "Der Bericht erscheint in wenigen Sekunden."); setOpen(r.id); qc.invalidateQueries({ queryKey: ["nightly"] }); },
    onError: (e: Error) => toast.error("Probelauf nicht gestartet", e.message),
  });
  return (
    <div className="card">
      <div className="card-head"><Moon size={18} color="var(--accent)" /><h3>Nachtlauf (Gesichter)</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>Modus</label>
          <select className="input" value={f.nightly_mode} onChange={(e) => patch({ nightly_mode: e.target.value as Settings["nightly_mode"] })}>
            <option value="off">Aus</option>
            <option value="preview">Probelauf (nur Bericht)</option>
            <option value="apply">Anwenden</option>
          </select>
          <span className="hint">{MODE_HINT[f.nightly_mode]}</span>
        </div>
        <div className="field"><label>Uhrzeit</label>
          <input type="time" className="input" style={{ width: 130 }} value={f.nightly_time} onChange={(e) => patch({ nightly_time: e.target.value })} />
          <span className="hint">Einmal je Tag; verpasste Läufe werden bis 6 h später nachgeholt.{st.data?.next_run ? ` Nächster Lauf: ${fmtDateTime(st.data.next_run)}.` : ""}</span>
        </div>
        <div className="field"><label>Sicher ab Ähnlichkeit</label>
          <Range value={f.nightly_auto_merge_min} min={0.3} max={0.95} step={0.01} onChange={(v) => patch({ nightly_auto_merge_min: v })} format={(v) => v.toFixed(2)} />
        </div>
        <div className="field"><label>… und Abstand zum Zweitbesten</label>
          <Range value={f.nightly_auto_merge_margin} min={0} max={0.5} step={0.01} onChange={(v) => patch({ nightly_auto_merge_margin: v })} format={(v) => v.toFixed(2)} />
        </div>
        <div className="field"><label>Rückfrage ab Ähnlichkeit</label>
          <Range value={f.nightly_review_min} min={0.2} max={0.95} step={0.01} onChange={(v) => patch({ nightly_review_min: v })} format={(v) => v.toFixed(2)} />
        </div>
        <div className="field"><label>Rückfragen je Lauf höchstens</label>
          <input type="number" min={0} max={50} className="input" value={f.nightly_review_max_per_day} onChange={(e) => patch({ nightly_review_max_per_day: Number(e.target.value) })} />
        </div>
        <div className="field"><label>Galerie kuratieren</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}><Switch checked={f.nightly_curate} onChange={(v) => patch({ nightly_curate: v })} label="Ausreißer markieren, Vielfalt" /></div>
        </div>
        <div className="field"><label>Schwelle je Person lernen</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}><Switch checked={f.nightly_thresholds} onChange={(v) => patch({ nightly_thresholds: v })} label="0,25 – 0,45" /></div>
        </div>
        <div className="field"><label>Einzelbild-Fragmente löschen nach</label>
          <div className="row"><input type="number" min={0} max={365} className="input" value={f.nightly_cleanup_days} onChange={(e) => patch({ nightly_cleanup_days: Number(e.target.value) })} /><span className="muted">Tagen (0 = aus)</span></div>
          <span className="hint">Nur unbenannt, ohne Ereignisse, mit höchstens einem Bild.</span>
        </div>
        <div className="field span-2" id="s-nachtlauf-berichte"><label>Probelauf und Berichte</label>
          <div className="row-wrap" style={{ gap: 8, alignItems: "center" }}>
            <button className="btn btn-sm" disabled={running || run.isPending || dirty} title={dirty ? "Erst speichern" : "Alle Schritte berechnen, nichts ändern"} onClick={() => run.mutate()}>
              {running || run.isPending ? <span className="spinner" /> : <Play size={14} />}Probelauf jetzt
            </button>
            {running && <span className="muted" style={{ fontSize: 13 }}>läuft …</span>}
          </div>
          {list.data && list.data.items.length > 0 ? (
            <div className="nl-reports">
              {list.data.items.map((r) => <ReportRow key={r.id} r={r} onOpen={() => setOpen(r.id)} />)}
            </div>
          ) : <span className="hint">Noch kein Bericht.</span>}
        </div>
      </div>
      {open !== null && <NightlyReportDialog id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function ReportRow({ r, onOpen }: { r: NightlyReportSummary; onOpen: () => void }) {
  const badge = r.status === "running" ? <span className="badge info">läuft</span>
    : r.status === "error" ? <span className="badge bad">Fehler</span>
      : r.mode === "preview" ? (r.applied_at ? <span className="badge ok">angewandt</span> : <span className="badge outline">Probelauf</span>)
        : <span className="badge accent">angewandt</span>;
  return (
    <button className="nl-report" onClick={onOpen}>
      <span className="muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>{fmtDateTime(r.started_at)}</span>
      {badge}
      <span className="nl-report-text">{r.error ?? r.text ?? ""}</span>
    </button>
  );
}

const TYPE_TITLE: Record<NightlyActionType, string> = {
  merge: "Zusammenführen", review: "Rückfragen", curate: "Galerie kuratieren", threshold: "Schwelle je Person", cleanup: "Aufräumen",
};
const TYPES: NightlyActionType[] = ["merge", "review", "curate", "threshold", "cleanup"];

function Face({ src, name, named }: { src?: string | null; name?: string; named?: boolean }) {
  return (
    <span className="nl-face">
      <Img src={src} className="nl-face-img" fallback={<span className="nl-face-img nl-face-empty"><User size={18} /></span>} />
      <span className={named ? "" : "dim"}>{name ?? "?"}</span>
    </span>
  );
}

const fmtNum = (v: number) => v.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function thresholdText(a: NightlyAction, glob?: number): string {
  const old = a.old ?? glob;
  const oldTxt = old != null ? fmtNum(old) : "global";
  if (a.new == null) return `Schwelle ${oldTxt}`;
  const hint = old == null ? "" : a.new < old ? " (erkennt großzügiger)" : a.new > old ? " (erkennt strenger)" : "";
  return `Schwelle ${oldTxt}${a.old == null ? " (global)" : ""} → ${fmtNum(a.new)}${hint}`;
}

function ActionLine({ a, glob }: { a: NightlyAction; glob?: number }) {
  if (a.type === "merge" || a.type === "review") {
    return (
      <div className="nl-pair">
        <Face src={a.src_cover_url} name={a.src_display_name} />
        <ArrowRight size={16} className="muted" />
        <Face src={a.into_cover_url} name={a.into_display_name} named={a.into_named} />
        <span className="muted nl-meta">
          {a.score != null && `Ähnlichkeit ${fmtNum(a.score)}`}
          {a.second_score != null && ` · Zweitbester ${fmtNum(a.second_score)}`}
          {a.samples != null && ` · ${a.samples} Bild${a.samples === 1 ? "" : "er"}`}
          {a.events != null && ` · ${a.events} Ereignis${a.events === 1 ? "" : "se"}`}
        </span>
      </div>
    );
  }
  const neg = a.negative_sample_ids?.length ?? 0;
  const rem = a.removed_sample_ids?.length ?? 0;
  return (
    <div className="nl-pair">
      <Face src={a.cover_url} name={a.display_name} named={a.type === "threshold"} />
      <span className="muted nl-meta">
        {a.type === "curate" && [neg ? `${neg} Ausreißer als Negativbeispiel` : "", rem ? `${rem} ähnliche Bilder entfernen` : ""].filter(Boolean).join(" · ")}
        {a.type === "threshold" && thresholdText(a, glob)}
        {a.type === "cleanup" && "Einzelbild ohne Ereignisse – löschen"}
      </span>
    </div>
  );
}

/** Bericht eines Nachtlaufs: Aktionen nach Art, im Probelauf auswählbar und anwendbar. */
export function NightlyReportDialog({ id, onClose }: { id: number; onClose: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["nightly", "report", id], queryFn: () => api.nightlyReport(id),
    refetchInterval: (x) => (x.state.data?.status === "running" ? 1500 : false) });
  const r: NightlyReport | undefined = q.data;
  const applicable = !!r && r.mode === "preview" && r.status === "done" && !r.applied_at;
  const [sel, setSel] = useState<Set<number> | null>(null);
  // Vorauswahl nur die sicheren Zusammenführungen; Rückfragen, Kuratieren, Schwellen und Aufräumen bewusst ankreuzen
  const selected = useMemo(() => sel ?? new Set((r?.actions ?? []).filter((a) => a.type === "merge").map((a) => a.idx)), [sel, r]);
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [confirm, setConfirm] = useState(false);
  const apply = useMutation({
    mutationFn: () => api.nightlyApply(id, selected.size === r!.actions.length ? null : [...selected]),
    onSuccess: () => {
      toast.success("Wird angewandt", "Zusammenführungen stehen danach im Verlauf und lassen sich zurücksetzen.");
      setConfirm(false); qc.invalidateQueries({ queryKey: ["nightly"] }); onClose();
    },
    onError: (e: Error) => { setConfirm(false); toast.error("Anwenden fehlgeschlagen", e.message); },
  });
  const toggle = (i: number) => { const n = new Set(selected); if (n.has(i)) n.delete(i); else n.add(i); setSel(n); };
  const count = (t: NightlyActionType) => r?.actions.filter((a) => a.type === t && selected.has(a.idx)).length ?? 0;
  return (
    <Dialog title={r ? `${r.mode === "preview" ? "Probelauf" : "Nachtlauf"} vom ${fmtDateTime(r.started_at)}` : "Nachtlauf-Bericht"} icon={<Moon size={18} />} size="lg" onClose={onClose}
      footer={applicable && r!.actions.length > 0 ? <>
        <button className="btn btn-ghost" onClick={onClose}>Schließen</button>
        <button className="btn btn-primary" disabled={!selected.size || apply.isPending} onClick={() => setConfirm(true)}>Jetzt anwenden ({selected.size})</button>
      </> : undefined}>
      {!r ? <Loading /> : r.status === "running" ? <Loading text="Nachtlauf läuft …" /> : (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div className={r.error ? "" : "dim"} style={r.error ? { color: "var(--bad, #f87171)" } : undefined}>{r.error ?? r.text}</div>
          {r.applied_at && <div className="hint">Angewandt am {fmtDateTime(r.applied_at)}.</div>}
          {r.actions.length === 0 && !r.error && <div className="muted">Keine Aktionen.</div>}
          {TYPES.map((t) => {
            const acts = r.actions.filter((a) => a.type === t);
            if (!acts.length) return null;
            return (
              <section key={t} className="nl-section">
                <h4>{TYPE_TITLE[t]} <span className="muted">({acts.length})</span></h4>
                {acts.map((a) => (
                  <label key={a.idx} className={`nl-action${a.skipped_reason ? " skipped" : ""}`}>
                    {applicable && <input type="checkbox" checked={selected.has(a.idx)} onChange={() => toggle(a.idx)} />}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <ActionLine a={a} glob={settings.data?.face_match_threshold} />
                      {a.skipped_reason && <div className="hint">übersprungen: {a.skipped_reason}</div>}
                    </div>
                    {r.mode === "apply" && !a.skipped_reason && <span className="badge ok">erledigt</span>}
                  </label>
                ))}
              </section>
            );
          })}
        </div>
      )}
      {confirm && r && (
        <ConfirmDialog title="Probelauf anwenden?" confirmLabel="Anwenden" danger={false} busy={apply.isPending}
          onClose={() => setConfirm(false)} onConfirm={() => apply.mutate()}
          text={<>
            {count("merge") > 0 && <div>{count("merge")} Fragment(e) zusammenführen (rücksetzbar im Verlauf)</div>}
            {count("review") > 0 && <div>{count("review")} Rückfrage(n) stellen</div>}
            {count("curate") > 0 && <div>{count("curate")} Galerie(n) kuratieren – entfernte Bilder sind danach gelöscht</div>}
            {count("threshold") > 0 && <div>{count("threshold")} Schwelle(n) je Person setzen</div>}
            {count("cleanup") > 0 && <div>{count("cleanup")} altes Einzelbild-Fragment / alte Fragmente löschen</div>}
            <div style={{ marginTop: 8 }}>Jede Aktion wird vorher neu geprüft; was sich inzwischen geändert hat, wird übersprungen.</div>
          </>} />
      )}
    </Dialog>
  );
}
