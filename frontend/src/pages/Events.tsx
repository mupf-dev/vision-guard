import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Clock, Film, History, Image as ImageIcon, LayoutGrid, List, RotateCcw, Zap, Camera as CamIcon } from "lucide-react";
import { api } from "../lib/api";
import type { VGEvent } from "../lib/types";
import { ALL_LABELS, KIND_NAME, labelColor, labelName } from "../lib/labels";
import { fmtDateTime, fmtDuration, fmtShort, localToIso } from "../lib/format";
import { Empty, Img, LabelIcon, LabelPill, Loading, SearchSelect } from "../components/ui";
import { EventDetailDialog } from "../components/EventBits";

const PAGE = 48;

export function EventGrid({ items, onPick }: { items: VGEvent[]; onPick: (e: VGEvent) => void }) {
  return (
    <div className="ev-grid">
      {items.map((e, i) => {
        const lc = labelColor(e.label);
        return (
          <div key={e.id} className="ev-card" style={{ ["--lc" as string]: lc, animationDelay: `${Math.min(i, 20) * 18}ms` }} onClick={() => onPick(e)} role="button" tabIndex={0} onKeyDown={(k) => k.key === "Enter" && onPick(e)}>
            <div className="media">
              <Img src={e.snapshot_url ?? e.thumb_url} fallback={<div style={{ height: "100%", display: "grid", placeItems: "center", color: lc }}><LabelIcon label={e.label} size={40} /></div>} />
              <div className="corner">
                <LabelPill label={e.label} score={e.score} />
                <span className="spacer" />
                {e.active && <span className="badge bad"><span className="dot" />aktiv</span>}
                {e.clip_url && <span className="badge"><Film size={12} /></span>}
              </div>
            </div>
            <div className="body">
              <div className="who">
                {e.thumb_url && e.snapshot_url && <img src={e.thumb_url} alt="" className="avatar" style={{ width: 24, height: 24, borderRadius: 6 }} />}
                {e.identity?.display_name ?? (e.attributes?.plate_text ? String(e.attributes.plate_text) : <span className="muted">Unbekannt</span>)}
              </div>
              <div className="meta"><span className="row" style={{ gap: 4 }}><CamIcon />{e.camera_name}</span><span className="row" style={{ gap: 4 }}><Clock />{fmtShort(e.start_ts)}</span></div>
              {e.rules?.length > 0 && <div className="meta" style={{ color: "var(--warn)" }}><Zap />{e.rules.join(", ")}</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function EventTable({ items, onPick }: { items: VGEvent[]; onPick: (e: VGEvent) => void }) {
  return (
    <div className="card" style={{ overflow: "auto" }}>
      <table className="table">
        <thead><tr><th></th><th>Objekt</th><th>Identität</th><th>Kamera</th><th>Zeit</th><th>Dauer</th><th>Regeln</th><th>Medien</th></tr></thead>
        <tbody>
          {items.map((e) => (
            <tr key={e.id} className="clickable" onClick={() => onPick(e)} role="button" tabIndex={0} onKeyDown={(k) => k.key === "Enter" && onPick(e)}>
              <td><Img src={e.thumb_url} className="thumb" /></td>
              <td><LabelPill label={e.label} score={e.score} /></td>
              <td>{e.identity?.display_name ?? (e.attributes?.plate_text ? String(e.attributes.plate_text) : <span className="muted">–</span>)}</td>
              <td>{e.camera_name}</td>
              <td className="mono" style={{ whiteSpace: "nowrap" }}>{fmtDateTime(e.start_ts)}</td>
              <td>{e.active ? <span className="badge bad"><span className="dot" />aktiv</span> : fmtDuration(e.start_ts, e.end_ts)}</td>
              <td>{e.rules?.length ? <span className="badge warn"><Zap size={11} />{e.rules.length}</span> : <span className="muted">–</span>}</td>
              <td><span className="feed-icons" style={{ margin: 0 }}><ImageIcon className={e.snapshot_url ? "has" : ""} /><Film className={e.clip_url ? "has" : ""} /></span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function EventsPage() {
  const [sp, setSp] = useSearchParams();
  const [view, setView] = useState<"grid" | "list">((localStorage.getItem("vg.evview") as "grid" | "list") ?? "grid");
  useEffect(() => localStorage.setItem("vg.evview", view), [view]);
  const [pickedState, setPicked] = useState<VGEvent | null>(null);
  // Deep-Link: /ereignisse?ev=<id>
  const evParam = sp.get("ev");
  const linked = useQuery({ queryKey: ["event", evParam], queryFn: () => api.event(evParam!), enabled: !!evParam });
  const picked = pickedState ?? (evParam ? linked.data ?? null : null);
  const closePicked = () => {
    setPicked(null);
    if (evParam) { const n = new URLSearchParams(sp); n.delete("ev"); setSp(n, { replace: true }); }
  };

  const f = {
    camera_id: sp.get("camera") ?? "",
    label: sp.get("label") ?? "",
    identity_id: sp.get("identity") ?? "",
    since: sp.get("since") ?? "",
    until: sp.get("until") ?? "",
    media: sp.get("media") ?? "",
    page: Number(sp.get("page") ?? 0),
  };
  const setF = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v); else n.delete(k);
    if (k !== "page") n.delete("page");
    setSp(n, { replace: true });
  };

  const cams = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const idents = useQuery({ queryKey: ["identities", "all"], queryFn: () => api.identities({}) });
  const q = useQuery({
    queryKey: ["events", "list", f],
    placeholderData: keepPreviousData,
    queryFn: () => api.events({
      camera_id: f.camera_id, label: f.label, identity_id: f.identity_id,
      since: localToIso(f.since), until: localToIso(f.until),
      has_snapshot: f.media === "snapshot" ? true : undefined,
      has_clip: f.media === "clip" ? true : undefined,
      limit: PAGE, offset: f.page * PAGE,
    }),
  });
  const items = q.data?.items ?? [];
  const total = q.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const hasFilter = [f.camera_id, f.label, f.identity_id, f.since, f.until, f.media].some(Boolean);

  return (
    <div className="page">
      <div className="page-header">
        <div><h1>Ereignisse</h1><div className="sub">{total.toLocaleString("de-DE")} Ereignisse{hasFilter ? " (gefiltert)" : ""}</div></div>
        <div className="seg">
          <button className={view === "grid" ? "on" : ""} onClick={() => setView("grid")}><LayoutGrid size={14} />Kacheln</button>
          <button className={view === "list" ? "on" : ""} onClick={() => setView("list")}><List size={14} />Liste</button>
        </div>
      </div>
      <div className="card filters">
        <div className="field"><label>Kamera</label>
          <select className="select input-sm" value={f.camera_id} onChange={(e) => setF("camera", e.target.value)}>
            <option value="">Alle Kameras</option>
            {cams.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
        <div className="field"><label>Objekt</label>
          <select className="select input-sm" value={f.label} onChange={(e) => setF("label", e.target.value)}>
            <option value="">Alle Objekte</option>
            {ALL_LABELS.map((l) => <option key={l} value={l}>{labelName(l)}</option>)}
          </select>
        </div>
        <div className="field" style={{ minWidth: 220 }}><label>Identität</label>
          <SearchSelect placeholder="Alle Identitäten" value={f.identity_id ? [Number(f.identity_id)] : []}
            onChange={(v) => setF("identity", v[0] ? String(v[0]) : "")}
            options={(idents.data ?? []).map((i) => ({ value: i.id, label: i.display_name, image: i.cover_url, sub: KIND_NAME[i.kind] }))} />
        </div>
        <div className="field"><label>Von</label><input type="datetime-local" className="input input-sm" value={f.since} onChange={(e) => setF("since", e.target.value)} /></div>
        <div className="field"><label>Bis</label><input type="datetime-local" className="input input-sm" value={f.until} onChange={(e) => setF("until", e.target.value)} /></div>
        <div className="field"><label>Medien</label>
          <select className="select input-sm" value={f.media} onChange={(e) => setF("media", e.target.value)}>
            <option value="">Alle</option><option value="snapshot">Nur mit Snapshot</option><option value="clip">Nur mit Clip</option>
          </select>
        </div>
        {hasFilter && <button className="btn btn-ghost btn-sm" onClick={() => setSp(new URLSearchParams(), { replace: true })}><RotateCcw size={14} />Zurücksetzen</button>}
      </div>

      {q.isLoading ? <Loading /> : q.isError ? <Empty title="Fehler beim Laden" text={(q.error as Error).message} />
        : items.length === 0 ? <Empty icon={<History size={44} />} title="Keine Ereignisse" text={hasFilter ? "Für diese Filter wurde nichts gefunden." : "Sobald Objekte erkannt werden, erscheinen sie hier."} />
        : view === "grid" ? <EventGrid items={items} onPick={setPicked} /> : <EventTable items={items} onPick={setPicked} />}

      {pages > 1 && (
        <div className="pager">
          <button className="btn btn-sm" disabled={f.page <= 0} onClick={() => setF("page", String(f.page - 1))}><ChevronLeft size={15} />Zurück</button>
          <span>Seite {f.page + 1} von {pages}</span>
          <button className="btn btn-sm" disabled={f.page + 1 >= pages} onClick={() => setF("page", String(f.page + 1))}>Weiter<ChevronRight size={15} /></button>
        </div>
      )}
      {picked && <EventDetailDialog event={picked} onClose={closePicked} />}
    </div>
  );
}
