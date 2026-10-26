import { useMemo, useState } from "react";
import { Link } from "react-router";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Clock, Droplet, Fingerprint, PawPrint, Toilet, UtensilsCrossed } from "lucide-react";
import type { ReactNode } from "react";
import { api } from "../lib/api";
import type { PetPurposeStats, PetStatus, PetSummary, PetVisit, ZonePurpose } from "../lib/types";
import { fmtAgo, fmtShort, parseTs } from "../lib/format";
import { Empty, Img, LabelIcon, Loading } from "../components/ui";
import { labelName } from "../lib/labels";
import { fmtNum, intlLocale, msgs, useMsgs } from "../i18n";

/**
 * Seite „Tiere“ – Haustier-Tagebuch (C3): je Tier Napf/Wasser/Klo heute vs. Normalwert (14 Tage), farbig bei
 * Abweichung, 14-Tage-Balken mit Normalbereich, darunter die Besuchsliste mit Vorschaubild. Aktualisiert sich über
 * WS `pet_visit` (useLiveSocket invalidiert ["pets"]).
 */

const PURPOSES: ZonePurpose[] = ["food", "water", "litter"];
const PURPOSE_ICON: Record<ZonePurpose, ReactNode> = {
  food: <UtensilsCrossed size={15} />, water: <Droplet size={15} />, litter: <Toilet size={15} />,
};
const STATUS_CLS: Record<PetStatus, string> = { ok: "ok", low: "warn", high: "bad", gap: "warn", unknown: "outline" };
const PAGE = 50;

function petKey(p: PetSummary) {
  return p.identity ? `id:${p.identity.id}` : `label:${p.label}`;
}

function petName(p: PetSummary) {
  return p.name ?? p.identity?.name ?? p.identity?.display_name ?? msgs().pets.unknownPet(labelName(p.label));
}

function fmtGap(s?: number | null) {
  if (s === null || s === undefined) return "–";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min`;
  const h = s / 3600;
  return h < 10 ? `${fmtNum(h, 1)} h` : `${Math.round(h)} h`;
}

function fmtDur(s: number | null) {
  if (s === null) return msgs().pets.running;
  if (s < 90) return `${Math.round(s)} s`;
  return `${Math.round(s / 60)} min`;
}

function range(r: [number, number] | null | undefined) {
  if (!r) return null;
  return r[0] === r[1] ? `${r[0]}` : `${r[0]}–${r[1]}`;
}

/** Balken der letzten Tage, Normalbereich als Band, heute hervorgehoben. */
function DailyBars({ st }: { st: PetPurposeStats }) {
  const max = Math.max(1, ...st.daily.map((d) => d.count), st.normal?.[1] ?? 0);
  const H = 44;
  return (
    <div className="pet-bars" style={{ height: H }} aria-hidden>
      {st.normal && (
        <div className="pet-band" style={{ bottom: (st.normal[0] / max) * H, height: Math.max(2, ((st.normal[1] - st.normal[0]) / max) * H) }} />
      )}
      {st.daily.map((d, i) => {
        const today = i === st.daily.length - 1;
        const day = new Date(`${d.day}T12:00:00`);
        return (
          <div key={d.day} className={`pet-bar ${today ? "today" : ""}`} title={`${day.toLocaleDateString(intlLocale(), { weekday: "short", day: "2-digit", month: "2-digit" })}: ${d.count}×`}>
            <div style={{ height: d.count ? Math.max(3, (d.count / max) * H) : 1 }} />
          </div>
        );
      })}
    </div>
  );
}

function PurposeTile({ purpose, st }: { purpose: ZonePurpose; st?: PetPurposeStats }) {
  const m = useMsgs().pets;
  if (!st) {
    return (
      <div className="pet-tile muted">
        <div className="pet-tile-head">{PURPOSE_ICON[purpose]}{m.purpose[purpose]}</div>
        <div style={{ fontSize: 13 }}>{m.noVisits}</div>
      </div>
    );
  }
  const status = st.status ?? (st.normal ? "ok" : "unknown");
  const s = { cls: STATUS_CLS[status], text: m.status[status] };
  const exp = range(st.expected_now);
  const normal = range(st.normal);
  return (
    <div className={`pet-tile st-${status}`}>
      <div className="pet-tile-head">
        {PURPOSE_ICON[purpose]}{m.purpose[purpose]}
        <span className="spacer" />
        <span className={`badge ${s.cls}`}>{s.text}</span>
      </div>
      <div className="row" style={{ alignItems: "baseline", gap: 8 }}>
        <span className="pet-count">{st.today}×</span>
        <span className="muted" style={{ fontSize: 13 }}>{m.today}</span>
      </div>
      <div className="muted" style={{ fontSize: 12, lineHeight: 1.5 }}>
        {normal ? <>{m.normalPerDay(normal)}{exp && exp !== normal ? <> · {m.soFar(exp)}</> : null}</> : m.normalAfter7}
        <br />
        {st.last_ts ? m.lastAt(fmtShort(st.last_ts), fmtAgo(st.last_ts)) : m.never}
        {status === "gap" && st.usual_gap_s ? <> · {m.usualGap(fmtGap(st.usual_gap_s))}</> : null}
      </div>
      <DailyBars st={st} />
    </div>
  );
}

function PetCard({ p, onSelect, selected }: { p: PetSummary; onSelect: () => void; selected: boolean }) {
  const m = useMsgs().pets;
  return (
    <div className={`card pet-card ${selected ? "sel" : ""}`}>
      <div className="card-head" style={{ cursor: "pointer" }} onClick={onSelect} title={m.showVisits}>
        <LabelIcon label={p.label} size={18} />
        <h3>{petName(p)}</h3>
        <span className="muted" style={{ fontSize: 12 }}>{labelName(p.label)}</span>
        <span className="spacer" />
        {p.identity && (
          <Link to={`/identitaeten/${p.identity.id}`} className="btn btn-ghost btn-sm" onClick={(e) => e.stopPropagation()}>
            <Fingerprint size={14} />{m.identity}
          </Link>
        )}
      </div>
      <div className="card-body pet-tiles">
        {PURPOSES.map((k) => <PurposeTile key={k} purpose={k} st={p.purposes[k]} />)}
      </div>
    </div>
  );
}

function VisitRow({ v }: { v: PetVisit }) {
  const m = useMsgs().pets;
  const name = v.identity?.name ?? v.identity?.display_name ?? m.unknownPet(labelName(v.label));
  return (
    <div className="pet-visit">
      <Img src={v.thumb_url} className="pet-thumb" fallback={<div className="pet-thumb ph"><LabelIcon label={v.label} size={20} /></div>} />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontWeight: 600 }}>{name} <span className="muted" style={{ fontWeight: 400 }}>· {m.purpose[v.purpose]}</span></div>
        <div className="muted" style={{ fontSize: 12 }}>{v.camera_name} · {v.zone_name}</div>
      </div>
      <div style={{ textAlign: "right", fontSize: 13 }}>
        <div><Clock size={12} style={{ verticalAlign: -1 }} /> {fmtShort(v.start_ts)}</div>
        <div className="muted" style={{ fontSize: 12 }}>{fmtDur(v.duration_s)}</div>
      </div>
    </div>
  );
}

function dayLabel(ts: string): string {
  const d = parseTs(ts);
  if (!d) return "–";
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const day = new Date(d); day.setHours(0, 0, 0, 0);
  const diff = Math.round((today.getTime() - day.getTime()) / 86400000);
  if (diff === 0) return msgs().pets.todayDay;
  if (diff === 1) return msgs().pets.yesterday;
  return d.toLocaleDateString(intlLocale(), { weekday: "long", day: "2-digit", month: "2-digit" });
}

export function PetsPage() {
  const m = useMsgs().pets;
  const [days, setDays] = useState(14);
  const [pet, setPet] = useState<string | null>(null);
  const [purpose, setPurpose] = useState<ZonePurpose | "">("");
  const [limit, setLimit] = useState(PAGE);
  const q = useQuery({ queryKey: ["pets", "summary", days], queryFn: () => api.petSummary(days), placeholderData: keepPreviousData, refetchInterval: 60_000 });
  const sel = useMemo(() => q.data?.find((p) => petKey(p) === pet) ?? null, [q.data, pet]);
  const visits = useQuery({
    queryKey: ["pets", "visits", pet, purpose, limit],
    queryFn: () => api.petVisits({
      limit, purpose: purpose || undefined,
      identity_id: sel ? (sel.identity ? sel.identity.id : 0) : undefined,
      label: sel && !sel.identity ? sel.label : undefined,
    }),
    placeholderData: keepPreviousData,
  });

  const groups = useMemo(() => {
    const out: { day: string; items: PetVisit[] }[] = [];
    for (const v of visits.data?.items ?? []) {
      const d = dayLabel(v.start_ts);
      if (!out.length || out[out.length - 1].day !== d) out.push({ day: d, items: [] });
      out[out.length - 1].items.push(v);
    }
    return out;
  }, [visits.data]);

  if (q.isLoading) return <Loading />;
  const pets = q.data ?? [];
  return (
    <div className="page">
      <div className="page-header">
        <div><h1>{m.title}</h1><div className="sub">{m.subtitle}</div></div>
        <div className="row">
          <select className="select input-sm" value={days} onChange={(e) => setDays(Number(e.target.value))} title={m.barsPeriod}>
            {[7, 14, 30].map((d) => <option key={d} value={d}>{m.nDays(d)}</option>)}
          </select>
        </div>
      </div>
      {!pets.length ? (
        <Empty icon={<PawPrint size={44} />} title={m.noVisitsYet}
          text={m.emptyText} />
      ) : (
        <>
          <div className="pet-grid">
            {pets.map((p) => (
              <PetCard key={petKey(p)} p={p} selected={petKey(p) === pet}
                onSelect={() => { setPet(petKey(p) === pet ? null : petKey(p)); setLimit(PAGE); }} />
            ))}
          </div>
          <div className="card" style={{ marginTop: 16 }}>
            <div className="card-head">
              <Clock size={18} color="var(--accent)" /><h3>{sel ? m.visitsOf(petName(sel)) : m.visits}</h3>
              <span className="muted" style={{ fontSize: 12 }}>{visits.data ? m.total(visits.data.total) : ""}</span>
              <span className="spacer" />
              {sel && <button className="btn btn-ghost btn-sm" onClick={() => setPet(null)}>{m.allPets}</button>}
              <select className="select input-sm" style={{ width: 140 }} value={purpose} onChange={(e) => { setPurpose(e.target.value as ZonePurpose | ""); setLimit(PAGE); }}>
                <option value="">{m.allPurposes}</option>
                {PURPOSES.map((k) => <option key={k} value={k}>{m.purpose[k]}</option>)}
              </select>
            </div>
            <div className="card-body">
              {!groups.length && <div className="muted">{m.noVisitsList}</div>}
              {groups.map((g) => (
                <div key={g.day} style={{ marginBottom: 12 }}>
                  <div className="muted" style={{ fontSize: 12, fontWeight: 600, textTransform: "uppercase", letterSpacing: ".05em", margin: "4px 0 6px" }}>{g.day}</div>
                  {g.items.map((v) => <VisitRow key={v.id} v={v} />)}
                </div>
              ))}
              {visits.data && visits.data.total > (visits.data.items.length ?? 0) && (
                <button className="btn btn-sm" onClick={() => setLimit((l) => l + PAGE)}>{m.loadMore}</button>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
