import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, Camera as CamIcon, Clock, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import type { NotificationSource, PushDelivery, VGNotification } from "../lib/types";
import { fmtAgo, fmtDateTime } from "../lib/format";
import { ConfirmDialog, Empty, Img, Loading } from "../components/ui";
import { toast } from "../lib/store";

/**
 * Seite „Hinweise“ – Grundgerüst aus K1 (Liste, Filter nach Quelle, Löschen, Zustellstatus).
 * A1 baut sie aus (Detailansicht, Regel-Bezug, Gegenstände, Bildvorschau groß …).
 */

export const SOURCE_NAME: Record<NotificationSource, string> = {
  rule: "Regel", health: "Kamera-Zustand", pet: "Tiere", review: "Rückfrage", report: "Bericht", sensor: "Sensor",
};

function DeliveryBadge({ d }: { d: PushDelivery }) {
  const cls = d.status === "sent" ? "ok" : d.status === "failed" ? "bad" : d.status === "skipped" ? "outline" : "warn";
  const txt = { sent: "zugestellt", failed: "fehlgeschlagen", skipped: "übersprungen", pending: "wird gesendet" }[d.status];
  return <span className={`badge ${cls}`} title={d.error ?? undefined}>{d.target}: {txt}</span>;
}

export function NotificationsPage() {
  const qc = useQueryClient();
  const [source, setSource] = useState<NotificationSource | "">("");
  const [del, setDel] = useState<VGNotification | null>(null);
  const q = useQuery({
    queryKey: ["notifications", source],
    queryFn: () => api.notifications({ source: source || undefined, limit: 200 }),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteNotification(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["notifications"] }); setDel(null); },
    onError: (e: Error) => toast.error("Löschen fehlgeschlagen", e.message),
  });
  const items = q.data?.items ?? [];

  return (
    <div className="page">
      <div className="page-header">
        <div><h1>Hinweise</h1><div className="sub">Meldungen aus Regeln, Kamera-Zustand und weiteren Quellen{q.data ? ` · ${q.data.total}` : ""}</div></div>
        <div className="seg">
          <button className={source === "" ? "on" : ""} onClick={() => setSource("")}>Alle</button>
          {(["rule", "health"] as const).map((s) => (
            <button key={s} className={source === s ? "on" : ""} onClick={() => setSource(s)}>{SOURCE_NAME[s]}</button>
          ))}
        </div>
      </div>
      {q.isLoading ? <Loading /> : !items.length ? (
        <Empty icon={<BellRing size={28} />} title="Keine Hinweise" text="Hinweise entstehen durch Regeln mit der Aktion „Push“ oder Bedingungen sowie durch Warnungen zum Kamera-Zustand." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {items.map((n) => (
            <div key={n.id} className="card">
              <div className="card-body" style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
                {n.image_url && (
                  <div style={{ width: 112, height: 72, flexShrink: 0, borderRadius: 8, overflow: "hidden", background: "var(--bg-4)" }}>
                    <Img src={n.image_url} className="cover-img" />
                  </div>
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                    <b>{n.title || "Hinweis"}</b>
                    <span className={`badge ${n.level === "alarm" ? "bad" : n.level === "warning" ? "warn" : "outline"}`}>{SOURCE_NAME[n.source] ?? n.source}</span>
                    {n.rule && n.source === "rule" && <span className="muted">Regel: {n.rule}</span>}
                  </div>
                  {n.message && <div style={{ marginTop: 4 }}>{n.message}</div>}
                  <div className="row muted" style={{ gap: 12, marginTop: 6, fontSize: 12, flexWrap: "wrap" }}>
                    <span className="row" style={{ gap: 4 }} title={fmtDateTime(n.ts)}><Clock size={12} />{fmtAgo(n.ts)}</span>
                    {n.camera_id !== null && <span className="row" style={{ gap: 4 }}><CamIcon size={12} />Kamera {n.camera_id}</span>}
                    {n.delivery.map((d) => <DeliveryBadge key={d.target} d={d} />)}
                  </div>
                </div>
                <button className="btn btn-ghost btn-icon btn-sm" title="Hinweis löschen" onClick={() => setDel(n)}><Trash2 size={15} /></button>
              </div>
            </div>
          ))}
        </div>
      )}
      {del && (
        <ConfirmDialog title="Hinweis löschen?" text={`„${del.title}“ vom ${fmtDateTime(del.ts)} wird entfernt.`}
          busy={remove.isPending} onConfirm={() => remove.mutate(del.id)} onClose={() => setDel(null)} />
      )}
    </div>
  );
}
