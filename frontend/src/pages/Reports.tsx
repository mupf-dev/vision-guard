import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight, Bell, Bike, Bot, Car, Dog, Eye, FileText, Package, PawPrint, Plus, Send, Trash2, TriangleAlert, Users, Workflow,
} from "lucide-react";
import { api } from "../lib/api";
import type { Report, ReportDelivery, ReportFacts } from "../lib/types";
import { fmtDateTime, parseTs } from "../lib/format";
import { ConfirmDialog, Empty, Img, Loading, Switch } from "../components/ui";
import { toast } from "../lib/store";
import { itemName } from "../components/ItemTeachDialog";
import { intlLocale, msgs, useMsgs } from "../i18n";

/**
 * Seite „Berichte“ (B3): Tagesberichte nach Datum, Ansicht mit Text oben und aufklappbaren Abschnitten (Personen,
 * Fahrzeuge, Tiere, Gegenstände mit Vorher/Nachher-Ausschnitten aus B2, Regeln, Hinweise), Vorschau ohne Speichern
 * und „Bericht für heute erstellen“ (optional mit Versand über alle eingerichteten Kanäle).
 */

const channelName = (k: string): string => msgs().reports.channel[k] ?? k;
/** Objektklasse (car, dog …) in der aktuellen Sprache, sonst der Name aus den Berichtsfakten */
const factLabel = (e: { label: string; name_de: string }): string => msgs().common.labels[e.label] ?? e.name_de;

function dayTitle(r: Report): string {
  const d = parseTs(r.period_end);
  if (!d) return "–";
  const day = d.toLocaleDateString(intlLocale(), { weekday: "long", day: "2-digit", month: "2-digit", year: "numeric" });
  return r.kind === "daily" ? day : `${day}, ${hmf().format(d)}`;
}

function generatorBadge(g: string) {
  const m = msgs().reports;
  if (g.startsWith("llm:")) return <span className="badge info" title={m.llmText}><Bot size={11} />{g.slice(4)}</span>;
  if (g.includes("Fallback")) return <span className="badge warn" title={m.llmRejected}><TriangleAlert size={11} />{m.templatesFallback}</span>;
  return <span className="badge outline">{m.templates}</span>;
}

function DeliveryBadges({ delivery }: { delivery: Record<string, ReportDelivery> }) {
  const m = useMsgs().reports;
  const keys = Object.keys(delivery);
  if (!keys.length) return <span className="badge outline">{m.notSent}</span>;
  return <>{keys.map((k) => {
    const d = delivery[k];
    const note = typeof d.note === "string" ? d.note : null;
    const cls = !d.ok ? "bad" : note ? "outline" : "ok";
    return <span key={k} className={`badge ${cls}`} title={d.error ?? note ?? (d.ts ? m.deliveredAt(fmtDateTime(d.ts)) : undefined)}>
      <Send size={11} />{channelName(k)}: {d.ok ? (note ? m.noteOnly : m.ok) : m.failed}
    </span>;
  })}</>;
}

function Section({ icon, title, summary, children, open }: { icon: ReactNode; title: string; summary?: string; children?: ReactNode; open?: boolean }) {
  return (
    <details className="card" open={open} style={{ padding: 0 }}>
      <summary style={{ cursor: "pointer", padding: "12px 16px", display: "flex", gap: 10, alignItems: "baseline", listStyle: "none" }}>
        <span style={{ color: "var(--accent)", alignSelf: "center", display: "flex" }}>{icon}</span>
        <strong style={{ flex: "none" }}>{title}</strong>
        {summary && <span className="dim" style={{ fontSize: 13, minWidth: 0 }}>{summary}</span>}
      </summary>
      {children && <div style={{ padding: "0 16px 14px" }}>{children}</div>}
    </details>
  );
}

const hmf = () => new Intl.DateTimeFormat(intlLocale(), { hour: "2-digit", minute: "2-digit" });
const dayf = () => new Intl.DateTimeFormat(intlLocale(), { day: "2-digit", month: "2-digit" });
type TimeFn = (v?: string | null) => string;

/** Uhrzeit „14:02“, an anderen Tagen als dem Berichtstag „24.10. 14:02“ (Bericht bis Mitternacht → Vortag) */
function timeFmt(endIso?: string): TimeFn {
  const end = parseTs(endIso);
  const ref = end && end.getHours() === 0 && end.getMinutes() === 0 ? new Date(end.getTime() - 1000) : end;
  const hm = hmf(), dm = dayf();
  return (v) => {
    const d = parseTs(v);
    if (!d) return "–";
    return ref && d.toDateString() === ref.toDateString() ? hm.format(d) : `${dm.format(d)} ${hm.format(d)}`;
  };
}

const span = (t: TimeFn, a?: string | null, b?: string | null) => {
  const x = t(a), y = t(b);
  return x === y ? x : `${x}–${y}`;
};

function Rows({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return <dl className="kv" style={{ gridTemplateColumns: "minmax(110px, auto) 1fr" }}>{rows.map(([k, v], i) => <div key={i} style={{ display: "contents" }}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>;
}

function Thumb({ src, label }: { src?: string | null; label: string }) {
  return (
    <div style={{ width: 92 }}>
      <div style={{ aspectRatio: "4 / 3", borderRadius: 6, overflow: "hidden", background: "var(--bg-4)" }}>
        <Img src={src} className="contain-img" fallback={<div className="muted" style={{ fontSize: 11, padding: 6, textAlign: "center" }}>–</div>} />
      </div>
      <div className="muted" style={{ fontSize: 11, textAlign: "center", marginTop: 2 }}>{label}</div>
    </div>
  );
}

function FactSections({ f, text }: { f: ReportFacts; text: Record<string, string> }) {
  const pe = f.people, ve = f.vehicles, an = f.animals, inv = f.inventory;
  const t = timeFmt(f.period?.end);
  const m = useMsgs().reports;
  const pm = useMsgs().pets.purpose as Record<string, string>;
  const srcName = useMsgs().notifications.source as Record<string, string>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <Section icon={<Users size={16} />} title={m.people} summary={text.people ?? text.quiet}>
        {pe.events > 0 && <Rows rows={[
          ...pe.named.map((p): [ReactNode, ReactNode] => [p.display_name, `${p.count}× · ${span(t, p.first, p.last)}${p.cameras.length ? ` · ${p.cameras.join(", ")}` : ""}`]),
          [m.unknownFaces, `${pe.unknown_faces}${pe.new_faces ? m.ofWhichNew(pe.new_faces) : ""}`],
          [m.withoutFace, `${pe.without_face}×`],
        ]} />}
      </Section>
      {ve.total > 0 && (
        <Section icon={<Car size={16} />} title={m.vehicles} summary={text.vehicles}>
          <Rows rows={[
            ...ve.by_label.map((e): [ReactNode, ReactNode] => [<span className="row" style={{ gap: 6 }}>{e.label === "bicycle" ? <Bike size={13} /> : <Car size={13} />}{factLabel(e)}</span>, `${e.count}× · ${span(t, e.first, e.last)}`]),
            ...ve.plates.map((p): [ReactNode, ReactNode] => [p.name ?? m.plate, `${p.plate_text ?? ""} · ${p.count}× · ${span(t, p.first, p.last)}`]),
          ]} />
        </Section>
      )}
      {(an.total > 0 || an.visits.length > 0) && (
        <Section icon={<Dog size={16} />} title={m.animals} summary={text.animals}>
          <Rows rows={[
            ...an.named.map((p): [ReactNode, ReactNode] => [p.display_name, `${p.count}× · ${span(t, p.first, p.last)}`]),
            ...an.by_label.map((e): [ReactNode, ReactNode] => [factLabel(e), m.inTotal(e.count)]),
            ...an.visits.map((v): [ReactNode, ReactNode] => [<span className="row" style={{ gap: 6 }}><PawPrint size={13} />{v.pet ?? v.label}</span>, `${pm[v.purpose] ?? v.purpose_de} ${v.count}× · ${m.lastAt(t(v.last))}`]),
          ]} />
        </Section>
      )}
      {(inv?.changes.length || f.inventory_error) ? (
        <Section icon={<Package size={16} />} title={m.items} summary={text.inventory}>
          {inv && <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {inv.changes.map((c, i) => (
              <div key={i} className="row" style={{ gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <Thumb src={c.before?.crop_url} label={c.before ? m.beforeAt(t(c.before.ts)) : m.before} />
                <ArrowRight size={14} color="var(--text-3)" />
                <Thumb src={c.after?.crop_url} label={c.after ? m.afterAt(t(c.after.ts)) : m.after} />
                <div style={{ flex: 1, minWidth: 180, fontSize: 13 }}><strong>{itemName(c as { name?: string; name_de: string })}</strong><div className="dim">{c.text}</div></div>
              </div>
            ))}
          </div>}
        </Section>
      ) : null}
      {f.rules.length > 0 && (
        <Section icon={<Workflow size={16} />} title={m.rules} summary={text.rules}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
            {f.rules.map((r) => (
              <div key={r.name} style={{ width: 180 }}>
                {r.snapshot_url && <div style={{ aspectRatio: "16 / 9", borderRadius: 6, overflow: "hidden", background: "var(--bg-4)", marginBottom: 4 }}><Img src={r.snapshot_url} className="cover-img" /></div>}
                <div style={{ fontSize: 13, fontWeight: 600 }}>{r.name}</div>
                <div className="muted" style={{ fontSize: 12 }}>{r.count}× · {m.lastAt(t(r.last))}</div>
              </div>
            ))}
          </div>
        </Section>
      )}
      {text.system && (
        <Section icon={<Bell size={16} />} title={m.notifications} summary={text.system}>
          {f.notifications.items.length > 0 && <Rows rows={f.notifications.items.map((n): [ReactNode, ReactNode] => [t(n.ts), `${srcName[n.source] ?? n.source_de}: ${n.title}`])} />}
        </Section>
      )}
    </div>
  );
}

function ReportView({ r, preview, onDelete }: { r: Report; preview?: boolean; onDelete?: () => void }) {
  const m = useMsgs().reports;
  const c = useMsgs().common;
  const f = r.facts;
  const text = Object.fromEntries((f?.sections ?? []).map((s) => [s.key, s.text]));
  const gen = f?.generation;
  const [first, ...rest] = r.text.split("\n");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
      <div className="card"><div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div className="row-wrap" style={{ gap: 8, alignItems: "center" }}>
          <h2 style={{ margin: 0, fontSize: 18, flex: 1, minWidth: 200 }}>{first}</h2>
          {preview ? <span className="badge warn"><Eye size={11} />{m.previewNotSaved}</span>
            : <button className="btn btn-sm btn-ghost" onClick={onDelete} title={m.deleteReport}><Trash2 size={14} />{c.delete}</button>}
        </div>
        <div className="row-wrap" style={{ gap: 6 }}>
          {generatorBadge(r.generator)}
          <span className="badge outline">{r.kind === "daily" ? m.daily : m.manual}</span>
          {!preview && <DeliveryBadges delivery={r.delivery} />}
        </div>
        {gen && !gen.ok && (
          <div className="dim" style={{ fontSize: 13, borderLeft: "3px solid var(--warn, #f59e0b)", paddingLeft: 10 }}>
            {m.llmNotUsed(gen.error ?? (gen.problems ?? []).join("; "))}
          </div>
        )}
        <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.55 }}>{rest.join("\n")}</div>
      </div></div>
      {f && <FactSections f={f} text={text} />}
    </div>
  );
}

export function ReportsPage() {
  const m = useMsgs().reports;
  const c = useMsgs().common;
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["reports"], queryFn: () => api.reports(60) });
  const [sel, setSel] = useState<number | null>(null);
  const [preview, setPreview] = useState<Report | null>(null);
  const [send, setSend] = useState(false);
  const [del, setDel] = useState<number | null>(null);
  const items = q.data?.items ?? [];
  const current = sel ?? items[0]?.id ?? null;
  useEffect(() => { if (sel !== null && items.length && !items.some((r) => r.id === sel)) setSel(null); }, [items, sel]);
  const detail = useQuery({ queryKey: ["report", current], enabled: current !== null && !preview, queryFn: () => api.report(current as number) });

  const pv = useMutation({
    mutationFn: () => api.reportPreview("today"),
    onSuccess: (r) => setPreview(r),
    onError: (e) => toast.error(m.previewFailed, (e as Error).message),
  });
  const gen = useMutation({
    mutationFn: () => api.generateReport({ since: "today", deliver: send }),
    onSuccess: (r) => {
      setPreview(null);
      setSel(r.id);
      qc.invalidateQueries({ queryKey: ["reports"] });
      const failed = Object.entries(r.delivery).filter(([, d]) => !d.ok).map(([k]) => channelName(k));
      if (failed.length) toast.error(m.reportCreated, m.sendFailed(failed.join(", ")));
      else toast.success(m.reportCreated, send ? m.andSent : undefined);
    },
    onError: (e) => toast.error(m.reportFailed, (e as Error).message),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteReport(id),
    onSuccess: () => { setDel(null); setSel(null); qc.invalidateQueries({ queryKey: ["reports"] }); toast.success(m.reportDeleted); },
  });

  if (q.isLoading) return <Loading />;
  return (
    <div className="page">
      <div className="page-header">
        <div><h1>{m.title}</h1><div className="sub">{m.subtitle}</div></div>
        <div className="row-wrap" style={{ gap: 8, alignItems: "center" }}>
          <Switch checked={send} onChange={setSend} label={m.alsoSend} />
          <button className="btn" disabled={pv.isPending} onClick={() => pv.mutate()}>{pv.isPending ? <span className="spinner" /> : <Eye size={15} />}{m.previewToday}</button>
          <button className="btn btn-primary" disabled={gen.isPending} onClick={() => gen.mutate()}>{gen.isPending ? <span className="spinner" /> : <Plus size={15} />}{m.createToday}</button>
        </div>
      </div>
      {!items.length && !preview ? (
        <Empty icon={<FileText size={44} />} title={m.noReports}
          text={m.emptyText} />
      ) : (
        <div className="reports-grid">
          <div className="list" style={{ gap: 6 }}>
            {preview && (
              <div className="list-item" style={{ cursor: "pointer", borderColor: "var(--accent)", padding: "10px 12px" }} onClick={() => undefined}>
                <div className="grow"><div className="title"><Eye size={14} />{m.preview}</div><div className="sub">{fmtDateTime(preview.period_end)}</div></div>
                <button className="btn btn-sm btn-ghost" onClick={() => setPreview(null)}>{c.close}</button>
              </div>
            )}
            {items.map((r) => (
              <div key={r.id} className="list-item" role="button" tabIndex={0}
                style={{ cursor: "pointer", padding: "10px 12px", borderColor: !preview && r.id === current ? "var(--accent)" : undefined }}
                onClick={() => { setPreview(null); setSel(r.id); }} onKeyDown={(k) => k.key === "Enter" && (setPreview(null), setSel(r.id))}>
                <div className="grow">
                  <div className="title" style={{ fontSize: 14 }}><FileText size={14} />{dayTitle(r)}</div>
                  <div className="sub">{r.text.split("\n")[1] ?? r.text}</div>
                </div>
                {Object.values(r.delivery).some((d) => !d.ok) && <TriangleAlert size={15} color="#fbbf24" />}
              </div>
            ))}
          </div>
          <div style={{ minWidth: 0 }}>
            {preview ? <ReportView r={preview} preview />
              : detail.data ? <ReportView r={detail.data} onDelete={() => setDel(detail.data!.id)} />
                : detail.isLoading ? <Loading /> : null}
          </div>
        </div>
      )}
      {del !== null && (
        <ConfirmDialog title={m.deleteTitle} text={m.deleteText}
          confirmLabel={c.delete} danger onConfirm={() => remove.mutate(del)} onClose={() => setDel(null)} />
      )}
    </div>
  );
}
