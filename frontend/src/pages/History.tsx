import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Camera as CamIcon, Clock, Film, History as HistoryIcon, Home, ListVideo, Search, Sparkles } from "lucide-react";
import { api } from "../lib/api";
import type { HistoryEpisode } from "../lib/types";
import { fmtShort, fmtTime, localToIso, parseTs } from "../lib/format";
import { Dialog, Empty, Loading } from "../components/ui";

/**
 * Seite „Zeitmaschine“ (W3/B1): Textsuche im Bildgedächtnis („roter Karton im Flur“) → Zeiträume je Kamera mit
 * Vorschau und Übereinstimmung; Klick → Filmstreifen ±10 min und Link zu den Ereignissen im selben Zeitraum.
 */

const PERIODS: { k: string; label: string }[] = [
  { k: "today", label: "Heute" },
  { k: "7d", label: "7 Tage" },
  { k: "all", label: "Alles" },
  { k: "custom", label: "Eigener Zeitraum" },
];

const EXAMPLES = ["Person mit Rucksack", "roter Karton", "Hund auf dem Sofa", "Auto in der Einfahrt", "offene Tür", "Licht an"];

function periodSince(k: string): string | undefined {
  const now = new Date();
  if (k === "today") { const d = new Date(now); d.setHours(0, 0, 0, 0); return d.toISOString(); }
  if (k === "7d") return new Date(now.getTime() - 7 * 86400_000).toISOString();
  return undefined;
}

/** ISO → Wert für <input type=datetime-local> bzw. den Events-Filter (lokale Zeit) */
function toLocalInput(iso: string, offsetS = 0): string {
  const d = parseTs(iso);
  if (!d) return "";
  const t = new Date(d.getTime() + offsetS * 1000 - d.getTimezoneOffset() * 60_000);
  return t.toISOString().slice(0, 16);
}

function spanText(e: { start_ts: string; end_ts: string }): string {
  const a = parseTs(e.start_ts), b = parseTs(e.end_ts);
  if (!a) return "–";
  const sameDay = b && a.toDateString() === b.toDateString();
  const day = a.toDateString() === new Date().toDateString() ? "Heute" : fmtShort(e.start_ts).split(",")[0];
  if (!b || b.getTime() - a.getTime() < 60_000) return `${day}, ${fmtTime(e.start_ts).slice(0, 5)}`;
  return sameDay ? `${day}, ${fmtTime(e.start_ts).slice(0, 5)} – ${fmtTime(e.end_ts).slice(0, 5)}` : `${fmtShort(e.start_ts)} – ${fmtShort(e.end_ts)}`;
}

function fmtBytes(n: number): string {
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(1).replace(".", ",")} MB`;
  return `${(n / 1024 ** 3).toFixed(2).replace(".", ",")} GB`;
}

/** Vorschau (256 px) mit optionalem Rahmen der Kachel, in der der Treffer lag */
function Preview({ src, bbox, alt, height }: { src: string | null; bbox?: [number, number, number, number] | null; alt: string; height?: number }) {
  const [err, setErr] = useState(false);
  useEffect(() => setErr(false), [src]);
  return (
    <div style={{ position: "relative", aspectRatio: height ? undefined : "16 / 9", height, borderRadius: 8, overflow: "hidden", background: "var(--bg-4)", display: "grid", placeItems: "center" }}>
      {src && !err
        ? <img src={src} alt={alt} loading="lazy" onError={() => setErr(true)} style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} />
        : <span className="muted" style={{ fontSize: 12 }}>keine Vorschau</span>}
      {bbox && src && !err && (
        <div title="Treffer in diesem Bildausschnitt" style={{
          position: "absolute", left: `${bbox[0] * 100}%`, top: `${bbox[1] * 100}%`, width: `${(bbox[2] - bbox[0]) * 100}%`,
          height: `${(bbox[3] - bbox[1]) * 100}%`, border: "2px solid var(--accent)", borderRadius: 4, boxShadow: "0 0 0 9999px rgba(0,0,0,.25)",
        }} />
      )}
    </div>
  );
}

function ScoreBar({ score }: { score: number }) {
  const c = score >= 70 ? "var(--ok)" : score >= 40 ? "var(--accent)" : "var(--text-3)";
  return (
    <div className="row" style={{ gap: 8, alignItems: "center" }} title="Ähnlichkeit zwischen Suchtext und Bild (keine sichere Erkennung)">
      <div style={{ flex: 1, height: 6, borderRadius: 3, background: "var(--bg-4)", overflow: "hidden" }}>
        <div style={{ width: `${Math.max(3, score)}%`, height: "100%", background: c }} />
      </div>
      <span style={{ fontSize: 12, fontVariantNumeric: "tabular-nums", color: c, minWidth: 34, textAlign: "right" }}>{score} %</span>
    </div>
  );
}

function EpisodeCard({ e, rank, onOpen }: { e: HistoryEpisode; rank: number; onOpen: () => void }) {
  const place = e.room && e.room !== e.camera_name ? `${e.room} · ${e.camera_name}` : e.camera_name;
  return (
    <div className="card" role="button" tabIndex={0} style={{ padding: 10, cursor: "pointer" }}
      onClick={onOpen} onKeyDown={(k) => k.key === "Enter" && onOpen()} title="Filmstreifen anzeigen">
      <Preview src={e.thumb_url} bbox={e.tile_bbox} alt={`${place} ${spanText(e)}`} />
      <div className="row" style={{ justifyContent: "space-between", marginTop: 8, gap: 8 }}>
        <span style={{ fontWeight: 600 }}>{spanText(e)}</span>
        <span className="badge outline" style={{ textTransform: "none", letterSpacing: 0 }}>#{rank}</span>
      </div>
      <div className="row muted" style={{ gap: 5, fontSize: 12.5, marginTop: 3, minWidth: 0 }}>
        {e.room ? <Home size={12} /> : <CamIcon size={12} />}<span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{place}</span>
        <span>· {e.frames} {e.frames === 1 ? "Bild" : "Bilder"}</span>
      </div>
      <div style={{ marginTop: 8 }}><ScoreBar score={e.score} /></div>
    </div>
  );
}

function FilmStrip({ e, onClose }: { e: HistoryEpisode; onClose: () => void }) {
  const q = useQuery({ queryKey: ["history", "frames", e.camera_id, e.best_ts], queryFn: () => api.historyFrames(e.camera_id, e.best_ts, 10) });
  const best = parseTs(e.best_ts)?.getTime() ?? 0;
  const items = q.data?.items ?? [];
  const bestIdx = useMemo(() => {
    let bi = -1, bd = Infinity;
    items.forEach((f, i) => { const d = Math.abs((parseTs(f.ts)?.getTime() ?? 0) - best); if (d < bd) { bd = d; bi = i; } });
    return bi;
  }, [items, best]);
  const [sel, setSel] = useState(-1);
  const cur = sel >= 0 ? items[sel] : items[bestIdx];
  const stripRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = stripRef.current?.querySelector<HTMLElement>("[data-best='1']");
    el?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [bestIdx]);
  const eventsLink = `/events?camera=${e.camera_id}&since=${encodeURIComponent(toLocalInput(e.start_ts, -60))}&until=${encodeURIComponent(toLocalInput(e.end_ts, 60))}`;
  return (
    <Dialog title={`${e.camera_name} · ${spanText(e)}`} icon={<Film size={18} />} onClose={onClose} size="xl"
      footer={<>
        <Link className="btn btn-ghost" to={eventsLink}><ListVideo size={15} />Ereignisse in diesem Zeitraum</Link>
        <button className="btn btn-primary" onClick={onClose}>Schließen</button>
      </>}>
      {q.isLoading ? <Loading /> : q.isError ? <Empty title="Filmstreifen nicht verfügbar" text={(q.error as Error).message} /> : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {cur ? (
            <div>
              <div style={{ maxWidth: 640, margin: "0 auto" }}>
                <Preview src={cur.thumb_url} bbox={sel < 0 || sel === bestIdx ? e.tile_bbox : null} alt={fmtTime(cur.ts)} />
              </div>
              <div className="muted" style={{ textAlign: "center", fontSize: 12.5, marginTop: 6 }}>
                {fmtShort(cur.ts)}{cur.last_ts && cur.last_ts !== cur.ts ? ` – unverändert bis ${fmtTime(cur.last_ts)}` : ""}
                {(sel < 0 || sel === bestIdx) && " · bester Treffer"}
              </div>
            </div>
          ) : <Empty title="Keine Bilder in diesem Zeitraum" text="Die Bilder wurden inzwischen gelöscht (Aufbewahrung)." />}
          {items.length > 0 && (
            <div ref={stripRef} style={{ display: "flex", gap: 6, overflowX: "auto", paddingBottom: 6, width: 0, minWidth: "100%" }}>
              {items.map((f, i) => (
                <button key={f.ts} data-best={i === bestIdx ? "1" : undefined} onClick={() => setSel(i)}
                  style={{ flex: "none", width: 132, padding: 3, borderRadius: 8, cursor: "pointer", background: "transparent",
                    border: `2px solid ${(sel < 0 ? bestIdx : sel) === i ? "var(--accent)" : "transparent"}` }}>
                  <Preview src={f.thumb_url} alt={fmtTime(f.ts)} />
                  <div style={{ fontSize: 11, marginTop: 3, color: i === bestIdx ? "var(--accent)" : "var(--text-3)" }}>{fmtTime(f.ts).slice(0, 5)}</div>
                </button>
              ))}
            </div>
          )}
          <div className="muted" style={{ fontSize: 12 }}>
            Gespeichert sind nur kleine Vorschaubilder ({items.length} im Bereich ±10 min um den besten Treffer), kein Video.
            Bilder, die sich kaum vom vorherigen unterscheiden, werden nicht erneut gespeichert.
          </div>
        </div>
      )}
    </Dialog>
  );
}

export function HistoryPage() {
  const [sp, setSp] = useSearchParams();
  const query = sp.get("q") ?? "";
  const camera = sp.get("camera") ?? "";
  const room = sp.get("room") ?? "";
  const period = sp.get("period") ?? "all";
  const from = sp.get("from") ?? "";
  const to = sp.get("to") ?? "";
  const [text, setText] = useState(query);
  useEffect(() => setText(query), [query]);
  const [open, setOpen] = useState<HistoryEpisode | null>(null);
  const setP = (k: string, v: string) => setSp((p) => { const n = new URLSearchParams(p); if (v) n.set(k, v); else n.delete(k); return n; }, { replace: true });

  const cams = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const stats = useQuery({ queryKey: ["history", "stats"], queryFn: api.historyStats, refetchInterval: 60_000 });
  const custom = period === "custom";
  const since = custom ? localToIso(from) : periodSince(period);
  const until = custom ? localToIso(to) : undefined;
  const res = useQuery({
    queryKey: ["history", "search", query, camera, room, since, until],
    queryFn: () => api.historySearch({ q: query, camera_id: camera ? Number(camera) : undefined, room: room || undefined, since, until, limit: 24 }),
    enabled: !!query, retry: false, placeholderData: keepPreviousData, staleTime: 30_000,
  });
  const camList = cams.data ?? [];
  const rooms = [...new Set(camList.map((c) => c.room).filter((r): r is string => !!r))].sort();
  const active = camList.filter((c) => c.history_enabled);
  const st = stats.data;

  const submit = (v: string) => { setP("q", v.trim()); };

  return (
    <div className="page">
      <div className="page-header">
        <div><h1>Zeitmaschine</h1><div className="sub">Was war wann zu sehen? Suche mit eigenen Worten im Bildgedächtnis der Kameras</div></div>
      </div>

      <form className="row-wrap" style={{ gap: 8, marginBottom: 12 }} onSubmit={(e) => { e.preventDefault(); submit(text); }}>
        <div style={{ position: "relative", flex: 1, minWidth: 220 }}>
          <Search size={16} style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)", color: "var(--text-3)" }} />
          <input className="input" style={{ width: "100%", paddingLeft: 34 }} placeholder="z. B. roter Karton im Flur" value={text}
            onChange={(e) => setText(e.target.value)} maxLength={200} aria-label="Suchtext" />
        </div>
        <button className="btn btn-primary" type="submit" disabled={!text.trim() || res.isFetching}>
          {res.isFetching ? <span className="spinner" /> : <Search size={15} />}Suchen
        </button>
      </form>

      <div className="card filters" style={{ marginBottom: 16 }}>
        <div className="field"><label>Zeitraum</label>
          <div className="seg">
            {PERIODS.map((p) => <button key={p.k} type="button" className={period === p.k ? "on" : ""} onClick={() => setP("period", p.k === "all" ? "" : p.k)}>{p.label}</button>)}
          </div>
        </div>
        {custom && <>
          <div className="field"><label>Von</label><input type="datetime-local" className="input input-sm" value={from} onChange={(e) => setP("from", e.target.value)} /></div>
          <div className="field"><label>Bis (leer = jetzt)</label><input type="datetime-local" className="input input-sm" value={to} onChange={(e) => setP("to", e.target.value)} /></div>
        </>}
        <div className="field"><label>Kamera</label>
          <select className="select input-sm" value={camera} onChange={(e) => setP("camera", e.target.value)}>
            <option value="">Alle Kameras</option>
            {camList.map((c) => <option key={c.id} value={c.id}>{c.name}{c.history_enabled ? "" : " (Zeitmaschine aus)"}</option>)}
          </select>
        </div>
        {rooms.length > 0 && (
          <div className="field"><label>Raum</label>
            <select className="select input-sm" value={room} onChange={(e) => setP("room", e.target.value)}>
              <option value="">Alle Räume</option>
              {rooms.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
        )}
      </div>

      {st && !st.enabled ? (
        <Empty icon={<HistoryIcon size={44} />} title="Zeitmaschine nicht verfügbar" text="Die Datenbank hat keine Vektorsuche (pgvector). Siehe scripts/db-migrate-pgvector.sh." />
      ) : !query ? (
        <Empty icon={<HistoryIcon size={44} />} title="Wonach suchen Sie?"
          text={active.length
            ? <>Beschreiben Sie, was zu sehen war – kurze, gegenständliche Beschreibungen funktionieren am besten. Aktiv auf {active.map((c) => c.name).join(", ")}; gespeichert: {st?.frames ?? 0} Bilder ({fmtBytes(st?.thumb_bytes ?? 0)}).</>
            : <>Noch keine Kamera zeichnet für die Zeitmaschine auf. Einschalten unter Administration → Kameras → „Zeitmaschine“.</>}>
          <div className="chips" style={{ justifyContent: "center", marginTop: 12 }}>
            {EXAMPLES.map((x) => <button key={x} type="button" className="chip" onClick={() => submit(x)}><Sparkles size={12} />{x}</button>)}
          </div>
        </Empty>
      ) : res.isLoading ? <Loading text="Suche im Bildgedächtnis …" /> : res.isError ? (
        <Empty icon={<HistoryIcon size={44} />} title="Suche nicht möglich" text={(res.error as Error).message} />
      ) : res.data && res.data.items.length ? (
        <>
          <div className="muted row-wrap" style={{ fontSize: 12.5, marginBottom: 10, gap: 6 }}>
            <Clock size={13} />{res.data.items.length} Zeiträume, beste Übereinstimmung zuerst
            {res.data.queries && res.data.queries.length > 1 && <span>· gesucht auch als „{res.data.queries.slice(1).join("“, „")}“</span>}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 12 }}>
            {res.data.items.map((e, i) => <EpisodeCard key={`${e.camera_id}-${e.start_ts}`} e={e} rank={i + 1} onOpen={() => setOpen(e)} />)}
          </div>
          <div className="muted" style={{ fontSize: 12, marginTop: 12 }}>
            Die Übereinstimmung misst, wie gut Bild und Beschreibung zusammenpassen – keine sichere Erkennung. Kleine Gegenstände
            findet die Suche eher bei Kameras mit eingeschalteten Kacheln.
          </div>
        </>
      ) : (
        <Empty icon={<HistoryIcon size={44} />} title="Nichts gefunden"
          text={`Für „${query}“ gibt es im gewählten Zeitraum keine gespeicherten Bilder${camera || room ? " dieser Kamera/dieses Raums" : ""}.`} />
      )}

      {open && <FilmStrip e={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
