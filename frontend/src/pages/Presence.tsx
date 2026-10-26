import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowRight, Footprints, MapPin, ScanFace, Shirt, UserRound } from "lucide-react";
import { api } from "../lib/api";
import type { Presence, PresenceSource } from "../lib/types";
import { fmtAgo, fmtDuration, fmtShort, fmtTime, pct } from "../lib/format";
import { Empty, Img, LabelIcon, Loading } from "../components/ui";
import { labelName } from "../lib/labels";
import { msgs, useMsgs } from "../i18n";

/**
 * Seite „Anwesenheit“ (W4 C2): wer ist gerade wo, wer wurde zuletzt wo gesehen. Quelle: GET /presence (RAM-Galerie
 * des Backends – Gesicht, Tiererkennung oder Wiedererkennung über Kleidung/Statur). WS `presence` invalidiert die
 * Liste (useLiveSocket), zusätzlich alle 30 s neu laden (relative Zeiten).
 */

const SOURCE: Record<PresenceSource, { text: () => string; icon: ReactNode; cls: string }> = {
  face: { text: () => msgs().presence.sourceFace, icon: <ScanFace size={12} />, cls: "ok" },
  pet: { text: () => msgs().presence.sourcePet, icon: <LabelIcon label="dog" size={12} />, cls: "ok" },
  reid: { text: () => msgs().presence.sourceReid, icon: <Shirt size={12} />, cls: "info" },
  none: { text: () => msgs().presence.sourceNone, icon: <UserRound size={12} />, cls: "outline" },
};

function who(p: Presence) {
  return p.identity?.display_name ?? msgs().presence.unknownWho(labelName(p.label));
}

function place(p: Presence) {
  const cam = p.camera_name ?? msgs().presence.camera(p.camera_id);
  return p.room && p.room !== cam ? `${p.room} · ${cam}` : cam;
}

function PresenceCard({ p }: { p: Presence }) {
  const m = useMsgs();
  const src = SOURCE[p.source] ?? SOURCE.none;
  // älteste zuerst → Weg durchs Haus; aufeinanderfolgende Tracks derselben Kamera zusammenfassen
  const trail = p.trail.slice(0, 8).reverse()
    .filter((t, i, a) => i === 0 || a[i - 1].camera_id !== t.camera_id).slice(-5);
  return (
    <div className={`card presence-card ${p.active ? "active" : ""}`}>
      <div className="presence-head">
        <Img src={p.thumb_url} className="presence-thumb" fallback={<div className="presence-thumb ph"><LabelIcon label={p.label} size={26} /></div>} />
        <div className="presence-main">
          <div className="presence-name">
            {p.identity ? <Link to={`/identitaeten/${p.identity.id}`}>{who(p)}</Link> : <span>{who(p)}</span>}
          </div>
          <div className="presence-where"><MapPin size={13} /> {place(p)}
            {p.zone_names.length > 0 && <span className="muted"> · {p.zone_names.join(", ")}</span>}
          </div>
          <div className="presence-when">
            {p.active
              ? <><span className="badge ok">{m.presence.now}</span>{m.presence.since(fmtTime(p.since), fmtDuration(p.since))}</>
              : <><span className="badge outline">{m.presence.last}</span> {fmtAgo(p.last_seen)} · {fmtShort(p.last_seen)}</>}
          </div>
        </div>
      </div>
      <div className="presence-foot">
        <span className={`badge ${src.cls}`} title={p.source === "reid" ? m.presence.reidHint : undefined}>
          {src.icon}{src.text()}{p.source === "reid" && p.confidence ? ` ${pct(p.confidence)}` : ""}
        </span>
        {trail.length > 1 && (
          <span className="presence-trail" title={m.presence.trailTitle}>
            {trail.map((t, i) => (
              <span key={`${t.camera_id}-${t.since}-${i}`} className="presence-step">
                {i > 0 && <ArrowRight size={11} className="muted" />}
                <span className={t.active ? "" : "muted"} title={`${fmtShort(t.since)}–${fmtTime(t.last_seen)}${t.via === "reid" ? m.presence.viaReid(t.score ? pct(t.score) : "") : t.via === "face" ? m.presence.viaFace : ""}`}>
                  {t.camera_name ?? t.camera_id}
                </span>
              </span>
            ))}
          </span>
        )}
      </div>
    </div>
  );
}

export function PresencePage() {
  const m = useMsgs();
  const [minutes, setMinutes] = useState(60);
  const [label, setLabel] = useState<"" | "person" | "dog,cat">("");
  const q = useQuery({
    queryKey: ["presence", "list", minutes, label],
    queryFn: () => api.presence(minutes, label || undefined),
    placeholderData: keepPreviousData, refetchInterval: 30_000,
  });
  const cams = useQuery({ queryKey: ["cameras"], queryFn: api.cameras, staleTime: 60_000 });
  const reidCams = (cams.data ?? []).filter((c) => c.reid_enabled).length;
  const items = q.data ?? [];
  const [now, before] = useMemo(() => [items.filter((p) => p.active), items.filter((p) => !p.active)], [items]);

  if (q.isLoading) return <Loading />;
  return (
    <div className="page">
      <div className="page-header">
        <div><h1>{m.presence.title}</h1>
          <div className="sub">{m.presence.subtitle}</div>
        </div>
        <div className="row">
          <select className="select input-sm" value={label} onChange={(e) => setLabel(e.target.value as typeof label)}>
            <option value="">{m.presence.all}</option><option value="person">{m.presence.people}</option><option value="dog,cat">{m.presence.animals}</option>
          </select>
          <select className="select input-sm" value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} title={m.presence.windowTitle}>
            <option value={15}>15 min</option><option value={60}>1 h</option><option value={180}>3 h</option><option value={720}>12 h</option>
          </select>
        </div>
      </div>
      {reidCams === 0 && cams.data && (
        <div className="hint-box" style={{ marginBottom: 12 }}>
          <Footprints size={15} /> {m.presence.noReid}
        </div>
      )}
      {!items.length ? (
        <Empty icon={<Footprints size={44} />} title={m.presence.emptyTitle}
          text={m.presence.emptyText(minutes)} />
      ) : (
        <>
          <h3 className="section-title">{m.presence.visibleNow} <span className="muted">({now.length})</span></h3>
          {now.length ? <div className="presence-grid">{now.map((p) => <PresenceCard key={p.presence_key} p={p} />)}</div>
            : <div className="muted" style={{ margin: "4px 0 16px" }}>{m.presence.nobodyNow}</div>}
          {before.length > 0 && <>
            <h3 className="section-title" style={{ marginTop: 20 }}>{m.presence.lastSeen} <span className="muted">({before.length})</span></h3>
            <div className="presence-grid">{before.map((p) => <PresenceCard key={p.presence_key} p={p} />)}</div>
          </>}
        </>
      )}
    </div>
  );
}
