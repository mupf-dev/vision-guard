import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Moon, Play, ScanFace, User } from "lucide-react";
import { api } from "../../lib/api";
import type { NightlyAction, NightlyActionType, NightlyReport, NightlyReportSummary, Settings } from "../../lib/types";
import { fmtDateTime } from "../../lib/format";
import { ConfirmDialog, Dialog, Img, Loading, Range, Switch } from "../../components/ui";
import { toast } from "../../lib/store";
import { fmtNum, msgs, useMsgs } from "../../i18n";

type Patch = (p: Partial<Settings>) => void;

/** Einstellungen → Gesichtserkennung (Identitäten 07, Pakete B/C; CONTRACT ID.E). */
export function FaceSettingsCard({ f, patch }: { f: Settings; patch: Patch }) {
  const all = useMsgs();
  const m = all.settings.face;
  return (
    <div className="card">
      <div className="card-head"><ScanFace size={18} color="var(--accent)" /><h3>{m.title}</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>{m.newMinQuality}</label>
          <Range value={f.face_new_min_quality} min={0} max={1} step={0.01} onChange={(v) => patch({ face_new_min_quality: v })} format={(v) => fmtNum(v, 2)} />
          <span className="hint">{m.newMinQualityHint}</span>
        </div>
        <div className="field"><label>{m.newMinSize}</label>
          <div className="row"><input type="number" min={0} max={1000} className="input" value={f.face_new_min_size} onChange={(e) => patch({ face_new_min_size: Number(e.target.value) })} /><span className="muted">px</span></div>
        </div>
        <div className="field"><label>{m.sampleMinQuality}</label>
          <Range value={f.face_sample_min_quality} min={0} max={1} step={0.01} onChange={(v) => patch({ face_sample_min_quality: v })} format={(v) => fmtNum(v, 2)} />
        </div>
        <div className="field"><label>{m.margin}</label>
          <Range value={f.face_match_margin} min={0} max={0.3} step={0.01} onChange={(v) => patch({ face_match_margin: v })} format={(v) => fmtNum(v, 2)} />
          <span className="hint">{m.marginHint}</span>
        </div>
        <div className="field"><label>{m.maxSamplesNamed}</label>
          <div className="row"><input type="number" min={20} max={100} className="input" value={f.face_max_samples_named} onChange={(e) => patch({ face_max_samples_named: Number(e.target.value) })} /><span className="muted">{m.unnamedLimit}</span></div>
        </div>
        <div className="field"><label>{m.trackFaces}</label>
          <div className="row"><input type="number" min={1} max={10} className="input" value={f.face_track_faces} onChange={(e) => patch({ face_track_faces: Number(e.target.value) })} /></div>
        </div>
        <div className="field span-2"><label>{m.fullres}</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}>
            <Switch checked={f.face_fullres} onChange={(v) => patch({ face_fullres: v })} label={f.face_fullres ? all.common.on : all.common.off} />
          </div>
          <span className="hint">{m.fullresHint}</span>
        </div>
      </div>
    </div>
  );
}

const NIGHTLY_KEYS = ["nightly_mode", "nightly_time", "nightly_auto_merge_min", "nightly_auto_merge_margin", "nightly_review_min",
  "nightly_review_max_per_day", "nightly_curate", "nightly_thresholds", "nightly_cleanup_days"] as const;

/** Einstellungen → Nachtlauf (Identitäten 07, Paket D; CONTRACT ID.H). */
export function NightlySettingsCard({ f, saved, patch }: { f: Settings; saved?: Settings; patch: Patch }) {
  const qc = useQueryClient();
  const all = useMsgs();
  const m = all.settings.nightly;
  const st = useQuery({ queryKey: ["nightly", "status"], queryFn: api.nightlyStatus,
    refetchInterval: (q) => (q.state.data?.running ? 2000 : false) });
  const list = useQuery({ queryKey: ["nightly", "reports"], queryFn: () => api.nightlyReports({ limit: 8 }) });
  const [open, setOpen] = useState<number | null>(null);
  const dirty = saved !== undefined && NIGHTLY_KEYS.some((k) => JSON.stringify(f[k]) !== JSON.stringify(saved[k]));
  const running = !!st.data?.running;
  const run = useMutation({
    mutationFn: () => api.nightlyRun("preview"),
    onSuccess: (r) => { toast.success(msgs().settings.nightly.toastStarted, msgs().settings.nightly.toastStartedText); setOpen(r.id); qc.invalidateQueries({ queryKey: ["nightly"] }); },
    onError: (e: Error) => toast.error(msgs().settings.nightly.toastStartFailed, e.message),
  });
  return (
    <div className="card">
      <div className="card-head"><Moon size={18} color="var(--accent)" /><h3>{m.title}</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>{m.mode}</label>
          <select className="input" value={f.nightly_mode} onChange={(e) => patch({ nightly_mode: e.target.value as Settings["nightly_mode"] })}>
            <option value="off">{m.modeOff}</option>
            <option value="preview">{m.modePreview}</option>
            <option value="apply">{m.modeApply}</option>
          </select>
          <span className="hint">{m.modeHint[f.nightly_mode]}</span>
        </div>
        <div className="field"><label>{m.time}</label>
          <input type="time" className="input" style={{ width: 130 }} value={f.nightly_time} onChange={(e) => patch({ nightly_time: e.target.value })} />
          <span className="hint">{m.timeHint}{st.data?.next_run ? m.nextRun(fmtDateTime(st.data.next_run)) : ""}</span>
        </div>
        <div className="field"><label>{m.autoMergeMin}</label>
          <Range value={f.nightly_auto_merge_min} min={0.3} max={0.95} step={0.01} onChange={(v) => patch({ nightly_auto_merge_min: v })} format={(v) => fmtNum(v, 2)} />
        </div>
        <div className="field"><label>{m.autoMergeMargin}</label>
          <Range value={f.nightly_auto_merge_margin} min={0} max={0.5} step={0.01} onChange={(v) => patch({ nightly_auto_merge_margin: v })} format={(v) => fmtNum(v, 2)} />
        </div>
        <div className="field"><label>{m.reviewMin}</label>
          <Range value={f.nightly_review_min} min={0.2} max={0.95} step={0.01} onChange={(v) => patch({ nightly_review_min: v })} format={(v) => fmtNum(v, 2)} />
        </div>
        <div className="field"><label>{m.reviewMax}</label>
          <input type="number" min={0} max={50} className="input" value={f.nightly_review_max_per_day} onChange={(e) => patch({ nightly_review_max_per_day: Number(e.target.value) })} />
        </div>
        <div className="field"><label>{m.curate}</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}><Switch checked={f.nightly_curate} onChange={(v) => patch({ nightly_curate: v })} label={m.curateLabel} /></div>
        </div>
        <div className="field"><label>{m.thresholds}</label>
          <div style={{ height: 38, display: "flex", alignItems: "center" }}><Switch checked={f.nightly_thresholds} onChange={(v) => patch({ nightly_thresholds: v })} label={`${fmtNum(0.25, 2)} – ${fmtNum(0.45, 2)}`} /></div>
        </div>
        <div className="field"><label>{m.cleanupDays}</label>
          <div className="row"><input type="number" min={0} max={365} className="input" value={f.nightly_cleanup_days} onChange={(e) => patch({ nightly_cleanup_days: Number(e.target.value) })} /><span className="muted">{m.cleanupUnit}</span></div>
          <span className="hint">{m.cleanupHint}</span>
        </div>
        <div className="field span-2" id="s-nachtlauf-berichte"><label>{m.reports}</label>
          <div className="row-wrap" style={{ gap: 8, alignItems: "center" }}>
            <button className="btn btn-sm" disabled={running || run.isPending || dirty} title={dirty ? all.settings.shared.saveFirst : m.runTitle} onClick={() => run.mutate()}>
              {running || run.isPending ? <span className="spinner" /> : <Play size={14} />}{m.runNow}
            </button>
            {running && <span className="muted" style={{ fontSize: 13 }}>{m.running}</span>}
          </div>
          {list.data && list.data.items.length > 0 ? (
            <div className="nl-reports">
              {list.data.items.map((r) => <ReportRow key={r.id} r={r} onOpen={() => setOpen(r.id)} />)}
            </div>
          ) : <span className="hint">{m.noReport}</span>}
        </div>
      </div>
      {open !== null && <NightlyReportDialog id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function ReportRow({ r, onOpen }: { r: NightlyReportSummary; onOpen: () => void }) {
  const m = useMsgs().settings.nightly;
  const badge = r.status === "running" ? <span className="badge info">{m.badgeRunning}</span>
    : r.status === "error" ? <span className="badge bad">{m.badgeError}</span>
      : r.mode === "preview" ? (r.applied_at ? <span className="badge ok">{m.badgeApplied}</span> : <span className="badge outline">{m.badgeDryRun}</span>)
        : <span className="badge accent">{m.badgeApplied}</span>;
  return (
    <button className="nl-report" onClick={onOpen}>
      <span className="muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>{fmtDateTime(r.started_at)}</span>
      {badge}
      <span className="nl-report-text">{r.error ?? r.text ?? ""}</span>
    </button>
  );
}

const TYPES: NightlyActionType[] = ["merge", "review", "curate", "threshold", "cleanup"];

function Face({ src, name, named }: { src?: string | null; name?: string; named?: boolean }) {
  return (
    <span className="nl-face">
      <Img src={src} className="nl-face-img" fallback={<span className="nl-face-img nl-face-empty"><User size={18} /></span>} />
      <span className={named ? "" : "dim"}>{name ?? "?"}</span>
    </span>
  );
}

const fmt2 = (v: number) => fmtNum(v, 2);

function thresholdText(a: NightlyAction, glob?: number): string {
  const m = msgs().settings.nightly;
  const old = a.old ?? glob;
  const oldTxt = old != null ? fmt2(old) : m.global;
  if (a.new == null) return m.threshold(oldTxt);
  const hint = old == null ? "" : a.new < old ? m.looser : a.new > old ? m.stricter : "";
  return `${m.threshold(oldTxt)}${a.old == null ? m.thresholdGlobal : ""} → ${fmt2(a.new)}${hint}`;
}

function ActionLine({ a, glob }: { a: NightlyAction; glob?: number }) {
  const m = useMsgs().settings.nightly;
  if (a.type === "merge" || a.type === "review") {
    return (
      <div className="nl-pair">
        <Face src={a.src_cover_url} name={a.src_display_name} />
        <ArrowRight size={16} className="muted" />
        <Face src={a.into_cover_url} name={a.into_display_name} named={a.into_named} />
        <span className="muted nl-meta">
          {a.score != null && m.similarity(fmt2(a.score))}
          {a.second_score != null && m.runnerUp(fmt2(a.second_score))}
          {a.samples != null && m.images(a.samples)}
          {a.events != null && m.events(a.events)}
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
        {a.type === "curate" && [neg ? m.outliers(neg) : "", rem ? m.removeSimilar(rem) : ""].filter(Boolean).join(" · ")}
        {a.type === "threshold" && thresholdText(a, glob)}
        {a.type === "cleanup" && m.cleanupLine}
      </span>
    </div>
  );
}

/** Bericht eines Nachtlaufs: Aktionen nach Art, im Probelauf auswählbar und anwendbar. */
export function NightlyReportDialog({ id, onClose }: { id: number; onClose: () => void }) {
  const qc = useQueryClient();
  const all = useMsgs();
  const m = all.settings.nightly;
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
      toast.success(msgs().settings.nightly.toastApplying, msgs().settings.nightly.toastApplyingText);
      setConfirm(false); qc.invalidateQueries({ queryKey: ["nightly"] }); onClose();
    },
    onError: (e: Error) => { setConfirm(false); toast.error(msgs().settings.nightly.toastApplyFailed, e.message); },
  });
  const toggle = (i: number) => { const n = new Set(selected); if (n.has(i)) n.delete(i); else n.add(i); setSel(n); };
  const count = (t: NightlyActionType) => r?.actions.filter((a) => a.type === t && selected.has(a.idx)).length ?? 0;
  return (
    <Dialog title={r ? m.dialogTitle(r.mode === "preview", fmtDateTime(r.started_at)) : m.dialogTitleLoading} icon={<Moon size={18} />} size="lg" onClose={onClose}
      footer={applicable && r!.actions.length > 0 ? <>
        <button className="btn btn-ghost" onClick={onClose}>{all.common.close}</button>
        <button className="btn btn-primary" disabled={!selected.size || apply.isPending} onClick={() => setConfirm(true)}>{m.applyNow(selected.size)}</button>
      </> : undefined}>
      {!r ? <Loading /> : r.status === "running" ? <Loading text={m.runningLong} /> : (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div className={r.error ? "" : "dim"} style={r.error ? { color: "var(--bad, #f87171)" } : undefined}>{r.error ?? r.text}</div>
          {r.applied_at && <div className="hint">{m.appliedAt(fmtDateTime(r.applied_at))}</div>}
          {r.actions.length === 0 && !r.error && <div className="muted">{m.noActions}</div>}
          {TYPES.map((t) => {
            const acts = r.actions.filter((a) => a.type === t);
            if (!acts.length) return null;
            return (
              <section key={t} className="nl-section">
                <h4>{m.types[t]} <span className="muted">({acts.length})</span></h4>
                {acts.map((a) => (
                  <label key={a.idx} className={`nl-action${a.skipped_reason ? " skipped" : ""}`}>
                    {applicable && <input type="checkbox" checked={selected.has(a.idx)} onChange={() => toggle(a.idx)} />}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <ActionLine a={a} glob={settings.data?.face_match_threshold} />
                      {a.skipped_reason && <div className="hint">{m.skipped(a.skipped_reason)}</div>}
                    </div>
                    {r.mode === "apply" && !a.skipped_reason && <span className="badge ok">{m.done}</span>}
                  </label>
                ))}
              </section>
            );
          })}
        </div>
      )}
      {confirm && r && (
        <ConfirmDialog title={m.confirmTitle} confirmLabel={all.common.apply} danger={false} busy={apply.isPending}
          onClose={() => setConfirm(false)} onConfirm={() => apply.mutate()}
          text={<>
            {count("merge") > 0 && <div>{m.confirmMerge(count("merge"))}</div>}
            {count("review") > 0 && <div>{m.confirmReview(count("review"))}</div>}
            {count("curate") > 0 && <div>{m.confirmCurate(count("curate"))}</div>}
            {count("threshold") > 0 && <div>{m.confirmThreshold(count("threshold"))}</div>}
            {count("cleanup") > 0 && <div>{m.confirmCleanup(count("cleanup"))}</div>}
            <div style={{ marginTop: 8 }}>{m.confirmNote}</div>
          </>} />
      )}
    </Dialog>
  );
}
