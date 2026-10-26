import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, Search, Square, Trash2, Tv } from "lucide-react";
import { api } from "../../lib/api";
import type { Settings, TvTarget } from "../../lib/types";
import { Switch } from "../../components/ui";
import { toast } from "../../lib/store";
import { msgs, useMsgs } from "../../i18n";

/**
 * Einstellungen → Fernseher (CONTRACT „Nachträge: Fernseher“): Fernseher im Heimnetz (DLNA), auf denen Regeln das
 * Livebild zeigen können, und die Adresse, unter der die Fernseher VisionGuard erreichen. Beides nur mit Anmeldung
 * im Browser änderbar.
 */

const ID_RE = /^[a-z0-9_-]{1,40}$/;

export function tvProblem(t: TvTarget): string | null {
  const m = msgs().settings;
  if (!ID_RE.test(t.id)) return m.shared.idProblem;
  if (!t.name.trim()) return m.shared.nameMissing;
  if (!t.description_url.trim()) return m.tv.addressMissing;
  return null;
}

function newTv(existing: TvTarget[]): TvTarget {
  let n = existing.length + 1;
  while (existing.some((t) => t.id === `tv${n}`)) n++;
  return { id: `tv${n}`, name: msgs().settings.tv.defaultName, enabled: true, type: "dlna", description_url: "" };
}

function TvEditor({ t, saved, onChange, onRemove }: { t: TvTarget; saved: TvTarget | undefined; onChange: (t: TvTarget) => void; onRemove: () => void }) {
  const all = useMsgs();
  const m = all.settings.tv;
  const [busy, setBusy] = useState<"probe" | "show" | "stop" | null>(null);
  const cams = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const [camId, setCamId] = useState<number | "">("");
  const problem = tvProblem(t);
  const unsaved = !saved || JSON.stringify(saved) !== JSON.stringify(t);
  const run = async (kind: "probe" | "show" | "stop", fn: () => Promise<void>) => {
    setBusy(kind);
    try { await fn(); } catch (e) { toast.error(msgs().settings.tv.toastTitle, (e as Error).message); } finally { setBusy(null); }
  };
  const probe = () => run("probe", async () => {
    const r = await api.tvProbe(t.description_url);
    if (r.ok) {
      const tm = msgs().settings.tv;
      toast.success(tm.toastFound, [r.name, r.model].filter(Boolean).join(" · ") || r.description_url);
      onChange({ ...t, description_url: r.description_url, name: t.name === tm.defaultName && r.name ? r.name : t.name });
    } else toast.error(msgs().settings.tv.toastNotFound, r.error ?? msgs().common.errorUnknown);
  });
  const cam = camId === "" ? (cams.data ?? [])[0]?.id : camId;
  const show = () => run("show", async () => {
    if (!cam) return;
    await api.tvShow(t.id, cam, 20);
    toast.success(msgs().settings.tv.toastShown, msgs().settings.tv.toastShownText);
  });
  const stop = () => run("stop", async () => { await api.tvStop(t.id); });
  return (
    <div className="action-card">
      <div className="ah">
        <span className="ico"><Tv size={15} /></span>DLNA
        <span className="spacer" />
        <Switch checked={t.enabled} onChange={(v) => onChange({ ...t, enabled: v })} label={all.common.active} />
        <button type="button" className="btn btn-sm" disabled={!!busy || !t.description_url.trim()} onClick={probe} title={m.probeTitle}>
          {busy === "probe" ? <span className="spinner" /> : <Search size={14} />}{m.probe}
        </button>
        <button type="button" className="btn btn-ghost btn-icon btn-sm" onClick={onRemove} title={m.remove}><Trash2 size={15} /></button>
      </div>
      <div className="form-grid">
        <div className="field"><label>{all.settings.shared.name}</label><input className="input input-sm" value={t.name} onChange={(e) => onChange({ ...t, name: e.target.value })} /></div>
        <div className="field"><label>{all.settings.shared.idForRules}</label><input className="input input-sm mono" value={t.id} onChange={(e) => onChange({ ...t, id: e.target.value.trim() })} /></div>
        <div className="field span-2"><label>{m.address}</label>
          <input className="input input-sm mono" value={t.description_url} placeholder={m.addressPlaceholder}
            onChange={(e) => onChange({ ...t, description_url: e.target.value })} />
          <span className="hint">{m.addressHint}</span>
        </div>
      </div>
      {problem && <span className="hint" style={{ color: "var(--warn)" }}>{problem}</span>}
      <div className="row-wrap" style={{ gap: 8 }}>
        <select className="select input-sm" style={{ maxWidth: 220 }} value={camId} aria-label={m.testCamera}
          onChange={(e) => setCamId(e.target.value ? Number(e.target.value) : "")}>
          {(cams.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <button type="button" className="btn btn-sm" disabled={!!busy || unsaved || !!problem || !cam} onClick={show}
          title={unsaved ? all.settings.shared.saveFirst : m.showTitle}>
          {busy === "show" ? <span className="spinner" /> : <Tv size={14} />}{m.show}
        </button>
        <button type="button" className="btn btn-sm btn-ghost" disabled={!!busy || unsaved} onClick={stop} title={m.stopTitle}>
          {busy === "stop" ? <span className="spinner" /> : <Square size={13} />}{m.stop}
        </button>
        {unsaved && <span className="muted" style={{ fontSize: 12 }}>{m.saveToTest}</span>}
      </div>
    </div>
  );
}

export function TvSettingsCard({ f, saved, patch }: { f: Settings; saved?: Settings; patch: (p: Partial<Settings>) => void }) {
  const m = useMsgs().settings.tv;
  const tvs = f.tv_targets ?? [];
  const setTvs = (t: TvTarget[]) => patch({ tv_targets: t });
  return (
    <div className="card">
      <div className="card-head"><Tv size={18} color="var(--accent)" /><h3>{m.title}</h3></div>
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div className="muted" style={{ fontSize: 12 }}>
          {m.intro}
        </div>
        <div className="field"><label>{m.baseUrl}</label>
          <input className="input mono" value={f.tv_stream_base_url ?? ""} placeholder="http://192.168.1.10:8090"
            onChange={(e) => patch({ tv_stream_base_url: e.target.value })} />
          <span className="hint">
            {m.baseUrlHint}
          </span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {tvs.map((t, i) => (
            <TvEditor key={i} t={t} saved={saved?.tv_targets?.[i]} onChange={(n) => setTvs(tvs.map((x, k) => (k === i ? n : x)))}
              onRemove={() => setTvs(tvs.filter((_, k) => k !== i))} />
          ))}
        </div>
        <div><button type="button" className="btn btn-sm" onClick={() => setTvs([...tvs, newTv(tvs)])}><Plus size={14} />{m.defaultName}</button></div>
      </div>
    </div>
  );
}
