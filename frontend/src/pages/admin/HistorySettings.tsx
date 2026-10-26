import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, Trash2 } from "lucide-react";
import { api } from "../../lib/api";
import type { Settings } from "../../lib/types";
import { fmtShort } from "../../lib/format";
import { toast } from "../../lib/store";
import { ConfirmDialog, Range } from "../../components/ui";
import { fmtNum, msgs, useMsgs } from "../../i18n";

function fmtBytes(n: number): string {
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  if (n < 1024 ** 3) return `${fmtNum(n / 1024 / 1024, 1)} MB`;
  return `${fmtNum(n / 1024 ** 3, 2)} GB`;
}

/** Einstellungen → Zeitmaschine (W3/B1): Aufbewahrung, Änderungsschwelle, Modell, Speicheranzeige, „Verlauf löschen“. */
export function HistorySettingsCard({ f, patch }: { f: Settings; patch: (p: Partial<Settings>) => void }) {
  const qc = useQueryClient();
  const all = useMsgs();
  const m = all.settings.history;
  const stats = useQuery({ queryKey: ["history", "stats"], queryFn: api.historyStats });
  const cams = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const [confirm, setConfirm] = useState(false);
  const del = useMutation({
    mutationFn: () => api.deleteHistory(),
    onSuccess: (r) => {
      toast.success(msgs().settings.history.toastDeleted, msgs().settings.history.toastDeletedText(r.deleted));
      qc.invalidateQueries({ queryKey: ["history"] });
      setConfirm(false);
    },
    onError: (e: Error) => toast.error(msgs().settings.history.toastDeleteFailed, e.message),
  });
  const st = stats.data;
  const name = (id: string) => cams.data?.find((c) => String(c.id) === id)?.name ?? m.camera(id);
  return (
    <div className="card">
      <div className="card-head"><Clock size={18} color="var(--accent)" /><h3>{m.title}</h3></div>
      <div className="card-body form-grid">
        <div className="field"><label>{m.retention}</label>
          <Range value={f.retention_days_history} min={1} max={90} step={1} format={(v) => all.settings.shared.days(v)} onChange={(v) => patch({ retention_days_history: v })} />
        </div>
        <div className="field"><label>{m.minChange}</label>
          <Range value={f.history_min_change} min={0} max={0.3} step={0.01} format={(v) => fmtNum(v, 2)} onChange={(v) => patch({ history_min_change: v })} />
          <span className="hint muted" style={{ fontSize: 12 }}>{m.minChangeHint(fmtNum(0.002), fmtNum(0.05))}</span>
        </div>
        <div className="field"><label>{m.model}</label>
          <select className="select" value={f.history_model} onChange={(e) => patch({ history_model: e.target.value as Settings["history_model"] })}>
            <option value="siglip2-b16-256">{m.modelSiglip}</option>
            <option value="mobileclip2-s0">{m.modelMobileclip}</option>
          </select>
          {stats.data?.model && stats.data.model !== f.history_model && (
            <span className="hint" style={{ fontSize: 12, color: "var(--warn)" }}>{m.modelChangeWarn}</span>
          )}
        </div>
        <div className="field"><label>{m.stored}</label>
          {st && !st.enabled ? <span className="muted">{m.unavailable}</span> : st ? (
            <div style={{ fontSize: 13 }}>
              <div>{m.summary(st.frames, fmtBytes(st.thumb_bytes), st.vectors)}</div>
              <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
                {st.oldest ? m.oldest(fmtShort(st.oldest)) : m.nothingYet}
                {Object.keys(st.by_camera).length > 0 && ` · ${Object.entries(st.by_camera).map(([id, n]) => `${name(id)}: ${n}`).join(", ")}`}
              </div>
              <button type="button" className="btn btn-ghost btn-sm" style={{ marginTop: 8, color: "var(--bad)" }} disabled={!st.frames}
                onClick={() => setConfirm(true)}><Trash2 size={14} />{m.deleteBtn}</button>
            </div>
          ) : <span className="muted">{all.common.loading}</span>}
        </div>
      </div>
      {confirm && (
        <ConfirmDialog title={m.confirmTitle} busy={del.isPending} onConfirm={() => del.mutate()} onClose={() => setConfirm(false)}
          text={<>{m.confirmText(st?.frames ?? 0)}</>} />
      )}
    </div>
  );
}
