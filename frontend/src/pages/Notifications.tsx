import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, Camera as CamIcon, Clock, ExternalLink, Package, RotateCcw, Send, Trash2, Workflow } from "lucide-react";
import { api } from "../lib/api";
import type { Camera, NotificationLevel, NotificationSource, PushDelivery, VGNotification } from "../lib/types";
import { fmtAgo, fmtDateTime, parseTs } from "../lib/format";
import { ConfirmDialog, Dialog, Empty, Img, Loading } from "../components/ui";
import { toast } from "../lib/store";

/**
 * Seite „Hinweise“ (A1): alle proaktiven Meldungen – Regeln mit Bedingungen/Push, Zeit-Regeln, Kamera-Zustand
 * und spätere Quellen. Filter nach Quelle, Stufe, Regel und Zeitraum (in der URL), nach Tagen gruppiert,
 * Detailansicht mit großem Bild, betroffenen Gegenständen, Ereignis-Link und Zustellstatus.
 */

export const SOURCE_NAME: Record<NotificationSource, string> = {
  rule: "Regel", health: "Kamera-Zustand", pet: "Tiere", review: "Rückfrage", report: "Bericht", sensor: "Sensor",
};
const LEVEL_NAME: Record<NotificationLevel, string> = { info: "Info", warning: "Warnung", alarm: "Alarm" };
const PERIODS: { id: string; name: string; hours: number | null }[] = [
  { id: "", name: "Alle", hours: null }, { id: "24h", name: "24 h", hours: 24 }, { id: "7d", name: "7 Tage", hours: 168 },
  { id: "30d", name: "30 Tage", hours: 720 },
];
const PAGE = 50;

function DeliveryBadge({ d }: { d: PushDelivery }) {
  const cls = d.status === "sent" ? "ok" : d.status === "failed" ? "bad" : d.status === "skipped" ? "outline" : "warn";
  const txt = { sent: "zugestellt", failed: "fehlgeschlagen", skipped: "übersprungen", pending: "wird gesendet" }[d.status];
  return <span className={`badge ${cls}`} title={d.error ?? undefined}><Send size={11} />{d.target}: {txt}</span>;
}

function levelBadge(n: VGNotification) {
  return n.level === "alarm" ? "bad" : n.level === "warning" ? "warn" : "outline";
}

function dayLabel(ts: string): string {
  const d = parseTs(ts);
  if (!d) return "–";
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const day = new Date(d); day.setHours(0, 0, 0, 0);
  const diff = Math.round((today.getTime() - day.getTime()) / 86400000);
  if (diff === 0) return "Heute";
  if (diff === 1) return "Gestern";
  return d.toLocaleDateString("de-DE", { weekday: "long", day: "2-digit", month: "2-digit", year: "numeric" });
}

function camName(cams: Camera[], id: number | null) {
  if (id === null) return null;
  return cams.find((c) => c.id === id)?.name ?? `Kamera ${id}`;
}

function NotificationDetail({ n, cams, onDelete, onClose }: { n: VGNotification; cams: Camera[]; onDelete: () => void; onClose: () => void }) {
  const items = useQuery({
    queryKey: ["notification-items", n.id, n.item_ids],
    enabled: n.item_ids.length > 0,
    queryFn: async () => (await Promise.allSettled(n.item_ids.slice(0, 8).map((id) => api.inventoryItem(id))))
      .flatMap((r) => (r.status === "fulfilled" ? [r.value] : [])),
  });
  const cam = camName(cams, n.camera_id);
  return (
    <Dialog size="lg" onClose={onClose} icon={<BellRing size={18} />} title={n.title || "Hinweis"} footer={<>
      <div className="spacer" />
      <button className="btn btn-ghost" onClick={onDelete}><Trash2 size={15} />Löschen</button>
      <button className="btn" onClick={onClose}>Schließen</button>
    </>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {n.image_url && (
          <div style={{ borderRadius: 10, overflow: "hidden", background: "var(--bg-4)", maxHeight: 420, display: "flex", justifyContent: "center" }}>
            <Img src={n.image_url} className="contain-img" />
          </div>
        )}
        {n.message && <div style={{ fontSize: 15 }}>{n.message}</div>}
        <div className="row-wrap" style={{ gap: 8 }}>
          <span className={`badge ${levelBadge(n)}`}>{LEVEL_NAME[n.level] ?? n.level}</span>
          <span className="badge outline">{SOURCE_NAME[n.source] ?? n.source}</span>
          <span className="badge outline" title={fmtDateTime(n.ts)}><Clock size={11} />{fmtDateTime(n.ts)}</span>
          {cam && <span className="badge outline"><CamIcon size={11} />{cam}</span>}
          {n.data?.trigger === "time" && <span className="badge outline">Zeit-Regel</span>}
        </div>
        {(n.rule || n.event_id) && (
          <div className="row-wrap" style={{ gap: 8 }}>
            {n.rule && n.rule_id !== null && <Link className="btn btn-sm" to="/admin/regeln"><Workflow size={14} />Regel „{n.rule}“</Link>}
            {n.event_id && <Link className="btn btn-sm" to={`/ereignisse?ev=${encodeURIComponent(n.event_id)}`}><ExternalLink size={14} />Ereignis ansehen</Link>}
          </div>
        )}
        {n.item_ids.length > 0 && (
          <div>
            <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>Betroffene Gegenstände</div>
            {items.isLoading ? <Loading /> : !items.data?.length ? <div className="muted" style={{ fontSize: 13 }}>Nicht mehr vorhanden.</div> : (
              <div className="row-wrap" style={{ gap: 10 }}>
                {items.data.map((it) => (
                  <Link key={it.id} to={`/gegenstaende?label=${encodeURIComponent(it.label)}&camera=${it.camera_id}`} className="card"
                    style={{ display: "flex", gap: 10, alignItems: "center", padding: 8, minWidth: 220, color: "inherit", textDecoration: "none" }}>
                    <div style={{ width: 56, height: 56, borderRadius: 8, overflow: "hidden", background: "var(--bg-4)", flexShrink: 0 }}>
                      <Img src={it.crop_url} className="cover-img" fallback={<Package size={20} />} />
                    </div>
                    <div style={{ minWidth: 0 }}>
                      <b>{it.name_de}</b>
                      <div className="muted" style={{ fontSize: 12 }}>{it.status_text ?? (it.visible ? "aktuell sichtbar" : "nicht sichtbar")}</div>
                      <div className="muted" style={{ fontSize: 12 }}>{[it.room ?? it.camera_name, ...(it.zone_names ?? [])].filter(Boolean).join(", ")}</div>
                    </div>
                  </Link>
                ))}
              </div>
            )}
          </div>
        )}
        <div>
          <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>Zustellung</div>
          {!n.delivery.length ? <div className="muted" style={{ fontSize: 13 }}>Kein Push (nur hier angezeigt).</div> : (
            <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
              {n.delivery.map((d) => (
                <div key={d.target} className="row" style={{ gap: 8 }}>
                  <DeliveryBadge d={d} />
                  {d.ts && <span className="muted">{fmtDateTime(d.ts)}</span>}
                  {d.error && <span className="muted">– {d.error}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </Dialog>
  );
}

export function NotificationsPage() {
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const f = { source: sp.get("source") ?? "", level: sp.get("level") ?? "", rule: sp.get("rule") ?? "", period: sp.get("period") ?? "" };
  const setF = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v); else n.delete(k);
    setSp(n, { replace: true });
    setLimit(PAGE);
  };
  const [limit, setLimit] = useState(PAGE);
  const [del, setDel] = useState<VGNotification | null>(null);
  const [open, setOpen] = useState<VGNotification | null>(null);
  const hours = PERIODS.find((p) => p.id === f.period)?.hours ?? null;
  // Zeitraum auf volle Minuten runden, damit der Query-Schlüssel stabil bleibt
  const since = useMemo(() => (hours ? new Date(Math.floor(Date.now() / 60000) * 60000 - hours * 3600000).toISOString() : undefined), [hours]);

  const cams = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const rules = useQuery({ queryKey: ["rules"], queryFn: api.rules });
  const q = useQuery({
    queryKey: ["notifications", f.source, f.level, f.rule, since, limit],
    placeholderData: keepPreviousData,
    queryFn: () => api.notifications({ source: f.source || undefined, level: f.level || undefined,
      rule_id: f.rule ? Number(f.rule) : undefined, since, limit }),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteNotification(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["notifications"] }); setDel(null); setOpen(null); },
    onError: (e: Error) => toast.error("Löschen fehlgeschlagen", e.message),
  });
  const items = q.data?.items ?? [];
  const total = q.data?.total ?? 0;
  const groups = useMemo(() => {
    const out: { day: string; items: VGNotification[] }[] = [];
    for (const n of items) {
      const day = dayLabel(n.ts);
      if (out.length && out[out.length - 1].day === day) out[out.length - 1].items.push(n);
      else out.push({ day, items: [n] });
    }
    return out;
  }, [items]);
  const hasFilter = !!(f.source || f.level || f.rule || f.period);
  const camList = cams.data ?? [];

  return (
    <div className="page">
      <div className="page-header">
        <div><h1>Hinweise</h1><div className="sub">Meldungen aus Regeln, Kamera-Zustand und weiteren Quellen{q.data ? ` · ${total}` : ""}</div></div>
      </div>
      <div className="row-wrap" style={{ gap: 10, marginBottom: 14 }}>
        <select className="select input-sm" style={{ width: "auto" }} value={f.source} onChange={(e) => setF("source", e.target.value)}>
          <option value="">Alle Quellen</option>
          {(Object.keys(SOURCE_NAME) as NotificationSource[]).map((s) => <option key={s} value={s}>{SOURCE_NAME[s]}</option>)}
        </select>
        <select className="select input-sm" style={{ width: "auto" }} value={f.level} onChange={(e) => setF("level", e.target.value)}>
          <option value="">Alle Stufen</option>
          {(Object.keys(LEVEL_NAME) as NotificationLevel[]).map((s) => <option key={s} value={s}>{LEVEL_NAME[s]}</option>)}
        </select>
        <select className="select input-sm" style={{ width: "auto", maxWidth: 240 }} value={f.rule} onChange={(e) => setF("rule", e.target.value)}>
          <option value="">Alle Regeln</option>
          {(rules.data ?? []).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
        </select>
        <div className="seg">
          {PERIODS.map((p) => <button key={p.id} className={f.period === p.id ? "on" : ""} onClick={() => setF("period", p.id)}>{p.name}</button>)}
        </div>
        {hasFilter && <button className="btn btn-ghost btn-sm" onClick={() => setSp(new URLSearchParams(), { replace: true })}><RotateCcw size={14} />Zurücksetzen</button>}
      </div>
      {q.isLoading ? <Loading /> : !items.length ? (
        <Empty icon={<BellRing size={28} />} title={hasFilter ? "Keine passenden Hinweise" : "Keine Hinweise"}
          text="Hinweise entstehen durch Regeln mit der Aktion „Push“ oder mit Bedingungen („Nur wenn …“), durch Zeit-Regeln sowie durch Warnungen zum Kamera-Zustand." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {groups.map((g) => (
            <div key={g.day} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div className="muted" style={{ fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".04em", marginTop: 6 }}>{g.day}</div>
              {g.items.map((n) => (
                <div key={n.id} className="card" style={{ cursor: "pointer" }} onClick={() => setOpen(n)}>
                  <div className="card-body" style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
                    <div style={{ width: 112, height: 72, flexShrink: 0, borderRadius: 8, overflow: "hidden", background: "var(--bg-4)", display: "grid", placeItems: "center" }}>
                      {n.image_url ? <Img src={n.image_url} className="cover-img" fallback={<BellRing size={22} className="muted" />} /> : <BellRing size={22} className="muted" />}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                        <b>{n.title || "Hinweis"}</b>
                        <span className={`badge ${levelBadge(n)}`}>{SOURCE_NAME[n.source] ?? n.source}</span>
                        {n.rule && n.source === "rule" && n.rule !== n.title && <span className="muted">Regel: {n.rule}</span>}
                      </div>
                      {n.message && <div style={{ marginTop: 4 }}>{n.message}</div>}
                      <div className="row muted" style={{ gap: 12, marginTop: 6, fontSize: 12, flexWrap: "wrap" }}>
                        <span className="row" style={{ gap: 4 }} title={fmtDateTime(n.ts)}><Clock size={12} />{fmtAgo(n.ts)}</span>
                        {n.camera_id !== null && <span className="row" style={{ gap: 4 }}><CamIcon size={12} />{camName(camList, n.camera_id)}</span>}
                        {n.item_ids.length > 0 && <span className="row" style={{ gap: 4 }}><Package size={12} />{n.item_ids.length === 1 ? "1 Gegenstand" : `${n.item_ids.length} Gegenstände`}</span>}
                        {n.delivery.map((d) => <DeliveryBadge key={d.target} d={d} />)}
                      </div>
                    </div>
                    <button className="btn btn-ghost btn-icon btn-sm" title="Hinweis löschen" onClick={(e) => { e.stopPropagation(); setDel(n); }}><Trash2 size={15} /></button>
                  </div>
                </div>
              ))}
            </div>
          ))}
          {total > items.length && (
            <button className="btn" style={{ alignSelf: "center" }} disabled={q.isFetching} onClick={() => setLimit((l) => l + PAGE)}>
              {q.isFetching && <span className="spinner" />}Weitere laden ({total - items.length})
            </button>
          )}
        </div>
      )}
      {open && <NotificationDetail n={open} cams={camList} onDelete={() => setDel(open)} onClose={() => setOpen(null)} />}
      {del && (
        <ConfirmDialog title="Hinweis löschen?" text={`„${del.title}“ vom ${fmtDateTime(del.ts)} wird entfernt.`}
          busy={remove.isPending} onConfirm={() => remove.mutate(del.id)} onClose={() => setDel(null)} />
      )}
    </div>
  );
}
