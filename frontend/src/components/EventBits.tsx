import { Fragment, useState } from "react";
import { Link } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera as CamIcon, CheckCheck, Clapperboard, Clock, Film, Image as ImageIcon, ScanFace, Siren, Trash2, Zap, MapPin, Fingerprint } from "lucide-react";
import { eventAlert, POSTURE_NAME } from "../lib/pose";
import type { Posture } from "../lib/types";
import type { Camera, VGEvent } from "../lib/types";
import { api } from "../lib/api";
import { labelColor } from "../lib/labels";
import { fmtAgo, fmtDateTime, fmtDuration, fmtTime } from "../lib/format";
import { ConfirmDialog, Dialog, Img, LabelIcon, LabelPill } from "./ui";
import { toast } from "../lib/store";

export function Plate({ text }: { text: string }) {
  return <span className="plate"><span>{text}</span></span>;
}

export function FeedItem({ e, onClick }: { e: VGEvent; onClick: () => void }) {
  const lc = labelColor(e.label);
  return (
    <div className={`feed-item ${e.active ? "active" : ""}`} style={{ ["--lc" as string]: lc }} onClick={onClick} role="button" tabIndex={0} onKeyDown={(k) => k.key === "Enter" && onClick()}>
      <div className="feed-thumb">
        <Img src={e.thumb_url} fallback={<div style={{ display: "grid", placeItems: "center", height: "100%", color: lc }}><LabelIcon label={e.label} size={26} /></div>} />
        {e.active && <span className="live-dot" title="aktiv" />}
      </div>
      <div className="feed-info">
        <div className="t1">
          <LabelPill label={e.label} />
          <span className="feed-icons">
            <ImageIcon className={e.snapshot_url ? "has" : ""} aria-label="Snapshot" />
            <Film className={e.clip_url ? "has" : ""} aria-label="Clip" />
          </span>
        </div>
        <div className="who">
          {e.identity?.display_name ?? (e.attributes?.plate_text ? String(e.attributes.plate_text) : <span className="muted">Unbekannt</span>)}
        </div>
        <div className="t2"><CamIcon />{e.camera_name}<Clock />{fmtTime(e.start_ts)}</div>
        {e.rules?.length > 0 && <div className="t2" style={{ color: "var(--warn)" }}><Zap />{e.rules.join(", ")}</div>}
        {eventAlert(e) && <AlertBadge e={e} />}
      </div>
    </div>
  );
}

/** W2 C1: Notfall-Kennzeichnung (offen / quittiert) */
export function AlertBadge({ e }: { e: VGEvent }) {
  const a = eventAlert(e);
  if (!a) return null;
  const p = a.posture ? POSTURE_NAME[a.posture] : "Alarm";
  return e.acknowledged_at
    ? <span className="badge outline" title={`quittiert ${fmtDateTime(e.acknowledged_at)}`}><CheckCheck size={11} />{p} · quittiert</span>
    : <span className="badge bad" title={`Regel „${a.rule}“`}><Siren size={11} />{p}{e.active ? "" : " · beendet"}</span>;
}

export function EventDetailDialog({ event, onClose }: { event: VGEvent; onClose: () => void }) {
  const qc = useQueryClient();
  const fresh = useQuery({ queryKey: ["event", event.id], queryFn: () => api.event(event.id), initialData: event, staleTime: 5000 });
  const e = fresh.data ?? event;
  const cams = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const cam: Camera | undefined = cams.data?.find((c) => c.id === e.camera_id);
  const zoneName = (id: string) => cam?.zones.find((z) => z.id === id)?.name ?? id;
  const [tab, setTab] = useState<"snapshot" | "clip" | "crop">(e.clip_url ? "clip" : e.snapshot_url ? "snapshot" : "crop");
  const [confirm, setConfirm] = useState(false);
  const del = useMutation({
    mutationFn: () => api.deleteEvent(e.id),
    onSuccess: () => { toast.success("Ereignis gelöscht"); qc.invalidateQueries({ queryKey: ["events"] }); onClose(); },
    onError: (err: Error) => toast.error("Löschen fehlgeschlagen", err.message),
  });
  const plate = e.attributes?.plate_text ? String(e.attributes.plate_text) : null;
  const alert = eventAlert(e);
  const ack = useMutation({
    mutationFn: () => api.ackEvent(e.id),
    onSuccess: (ev) => { toast.success("Quittiert"); qc.setQueryData(["event", e.id], ev); qc.invalidateQueries({ queryKey: ["events"] }); },
    onError: (err: Error) => toast.error("Quittieren fehlgeschlagen", err.message),
  });
  const HIDDEN = new Set(["plate_text", "alert", "posture", "posture_since", "motion", "presence_key", "identity_source"]);
  const viaReid = e.attributes?.identity_source === "reid";
  const extraAttrs = Object.entries(e.attributes ?? {}).filter(([k, v]) => !HIDDEN.has(k) && v !== null && v !== undefined && v !== "" && typeof v !== "object");
  const posture = e.attributes?.posture ? String(e.attributes.posture) as Posture : null;

  return (
    <Dialog size="lg" onClose={onClose} icon={<LabelPill label={e.label} score={e.score} />}
      title={<span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.identity?.display_name ?? "Unbekannt"} · {e.camera_name}</span>}
      footer={<>
        <button className="btn btn-danger" onClick={() => setConfirm(true)}><Trash2 size={16} />Löschen</button>
        <span className="spacer" />
        {alert && !e.acknowledged_at && <button className="btn btn-danger" disabled={ack.isPending} onClick={() => ack.mutate()}>{ack.isPending ? <span className="spinner" /> : <CheckCheck size={16} />}Quittieren</button>}
        {e.snapshot_url && <a className="btn" href={e.snapshot_url} target="_blank" rel="noreferrer" download><ImageIcon size={16} />Snapshot</a>}
        {e.clip_url && <a className="btn" href={e.clip_url} target="_blank" rel="noreferrer" download><Film size={16} />Clip</a>}
        <button className="btn btn-primary" onClick={onClose}>Schließen</button>
      </>}>
      <div className="ev-detail">
        <div>
          <div className="media-tabs seg">
            <button className={tab === "clip" ? "on" : ""} disabled={!e.clip_url} onClick={() => setTab("clip")}><Clapperboard size={14} />Clip</button>
            <button className={tab === "snapshot" ? "on" : ""} disabled={!e.snapshot_url} onClick={() => setTab("snapshot")}><ImageIcon size={14} />Snapshot</button>
            <button className={tab === "crop" ? "on" : ""} disabled={!e.thumb_url} onClick={() => setTab("crop")}><ScanFace size={14} />Ausschnitt</button>
          </div>
          <div className="media-view">
            {tab === "clip" && e.clip_url ? <video key={e.clip_url} src={e.clip_url} controls autoPlay muted playsInline />
              : tab === "snapshot" && e.snapshot_url ? <img src={e.snapshot_url} alt="Snapshot" />
              : e.thumb_url ? <img src={e.thumb_url} alt="Ausschnitt" />
              : <span className="muted">Kein Bildmaterial</span>}
          </div>
          {e.active && !e.clip_url && <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>Ereignis läuft noch – Clip wird ggf. nach Ende erstellt.</div>}
        </div>
        <div className="col" style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          {e.thumb_url && <img src={e.thumb_url} alt="" style={{ width: "100%", maxHeight: 220, objectFit: "contain", borderRadius: 10, background: "var(--bg-1)", border: "1px solid var(--line)" }} />}
          <dl className="kv">
            <dt>Status</dt><dd>{e.active ? <span className="badge bad"><span className="dot" />aktiv</span> : <span className="badge">beendet</span>}</dd>
            <dt>Kamera</dt><dd>{e.camera_name}</dd>
            <dt>Beginn</dt><dd>{fmtDateTime(e.start_ts)}</dd>
            <dt>Dauer</dt><dd>{fmtDuration(e.start_ts, e.end_ts)}</dd>
            <dt>Konfidenz</dt><dd>{Math.round(e.score * 100)} %</dd>
            <dt>Track</dt><dd className="mono">#{e.track_id}</dd>
            <dt>Identität</dt>
            <dd>{e.identity ? <Link to={`/identitaeten/${e.identity.id}`} onClick={onClose}><Fingerprint size={13} style={{ verticalAlign: -2 }} /> {e.identity.display_name}</Link> : <span className="muted">–</span>}
              {e.identity && viaReid && <span className="muted" title="Kein eigenes Gesicht – über Kleidung/Statur von einer anderen Kamera übernommen (nur ein Hinweis)"> · über Wiedererkennung</span>}
              {e.attributes?.presence_key ? <> · <Link to="/anwesenheit" onClick={onClose} className="muted">Anwesenheit</Link></> : null}</dd>
            {plate && <><dt>Kennzeichen</dt><dd><Plate text={plate} /></dd></>}
            {posture && <><dt>Haltung</dt><dd>{POSTURE_NAME[posture] ?? posture}{typeof e.attributes?.motion === "number" ? <span className="muted"> · Bewegung {Number(e.attributes.motion).toFixed(2)}</span> : null}</dd></>}
            {alert && <><dt>Notfall</dt><dd><AlertBadge e={e} />{alert.repeat ? <span className="muted"> · {alert.repeat}× wiederholt</span> : null}{e.acknowledged_at ? <span className="muted"> · {fmtDateTime(e.acknowledged_at)}</span> : null}</dd></>}
            {extraAttrs.map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>)}
            <dt>Zonen</dt>
            <dd>{e.zones?.length ? <span className="row-wrap" style={{ gap: 4 }}>{e.zones.map((z) => <span key={z} className="badge outline"><MapPin size={11} />{zoneName(z)}</span>)}</span> : <span className="muted">–</span>}</dd>
            <dt>Regeln</dt>
            <dd>{e.rules?.length ? <span className="row-wrap" style={{ gap: 4 }}>{e.rules.map((r) => <span key={r} className="badge warn"><Zap size={11} />{r}</span>)}</span> : <span className="muted">keine</span>}</dd>
          </dl>
          <div className="muted" style={{ fontSize: 12 }}>ID <span className="mono">{e.id}</span> · {fmtAgo(e.start_ts)}</div>
        </div>
      </div>
      {confirm && <ConfirmDialog title="Ereignis löschen?" text="Das Ereignis inklusive Snapshot und Clip wird dauerhaft gelöscht." busy={del.isPending}
        onConfirm={() => del.mutate()} onClose={() => setConfirm(false)} />}
    </Dialog>
  );
}
