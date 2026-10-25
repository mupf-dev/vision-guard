import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowRight, Camera as CamIcon, Clock, Home, MessageSquareText, MoveRight, Package, PackageMinus, PackagePlus, Timer, TriangleAlert } from "lucide-react";
import { api } from "../lib/api";
import type { Camera, InventoryChange, InventoryChangeType, InventoryItem } from "../lib/types";
import { fmtAgo, fmtTime, localToIso } from "../lib/format";
import { Empty, Loading } from "../components/ui";

/** Zeitraum-Chips (Werte = `since` laut CONTRACT W1.E) */
const PERIODS: { k: string; label: string }[] = [
  { k: "morning", label: "Seit heute Morgen" },
  { k: "3h", label: "Letzte 3 h" },
  { k: "yesterday", label: "Seit gestern" },
  { k: "custom", label: "Eigener Zeitraum" },
];

const COLS: { type: InventoryChangeType; title: string; icon: ReactNode; color: string; empty: string }[] = [
  { type: "appeared", title: "Neu", icon: <PackagePlus size={15} />, color: "var(--ok)", empty: "Nichts Neues aufgetaucht." },
  { type: "disappeared", title: "Weg", icon: <PackageMinus size={15} />, color: "var(--bad)", empty: "Nichts verschwunden." },
  { type: "moved", title: "Verschoben", icon: <MoveRight size={15} />, color: "var(--accent)", empty: "Nichts verschoben." },
];

const placeOf = (it: InventoryItem) => it.room && it.room !== it.camera_name ? `${it.room} · ${it.camera_name}` : it.camera_name;

function Thumb({ src, label, empty }: { src?: string | null; label: string; empty: string }) {
  const [err, setErr] = useState(false);
  useEffect(() => setErr(false), [src]);
  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ aspectRatio: "4 / 3", borderRadius: 8, overflow: "hidden", background: "var(--bg-4)", display: "grid", placeItems: "center" }}>
        {src && !err
          ? <img src={src} alt={label} loading="lazy" onError={() => setErr(true)} style={{ width: "100%", height: "100%", objectFit: "contain", display: "block" }} />
          : <div style={{ color: "var(--text-3)", fontSize: 12, textAlign: "center", padding: 6 }}>{empty}</div>}
      </div>
      <div className="muted" style={{ fontSize: 11, marginTop: 3, textAlign: "center" }}>{label}</div>
    </div>
  );
}

function ChangeCard({ c, color, onPick }: { c: InventoryChange; color: string; onPick: (it: InventoryItem) => void }) {
  const it = c.item;
  const beforeEmpty = c.type === "appeared" || c.type === "transient" ? "noch nicht da" : "kein Ausschnitt";
  const afterEmpty = c.type === "disappeared" ? "nicht mehr da" : c.type === "uncertain" ? "Stand unklar" : "kein Ausschnitt";
  return (
    <div className="card" role="button" tabIndex={0} style={{ padding: 10, cursor: "pointer", borderLeft: `3px solid ${color}` }}
      onClick={() => onPick(it)} onKeyDown={(k) => k.key === "Enter" && onPick(it)} title="Details anzeigen">
      <div className="row" style={{ gap: 6, alignItems: "center" }}>
        <Thumb src={c.before?.crop_url} label={c.before ? `vorher ${fmtTime(c.before.ts)}` : "vorher"} empty={beforeEmpty} />
        <ArrowRight size={16} color="var(--text-3)" style={{ flex: "none" }} />
        <Thumb src={c.after?.crop_url} label={c.after ? `nachher ${fmtTime(c.after.ts)}` : "nachher"} empty={afterEmpty} />
      </div>
      <div style={{ fontWeight: 600, marginTop: 8 }} className="row"><Package size={14} color={color} style={{ marginRight: 6 }} />{it.name_de}</div>
      <div className="dim" style={{ fontSize: 12.5, marginTop: 3 }}>{c.text}</div>
      <div className="meta row" style={{ gap: 10, marginTop: 6, fontSize: 12, color: "var(--text-3)", flexWrap: "wrap" }}>
        <span className="row" style={{ gap: 4 }}><Clock size={12} />{fmtTime(c.at)} · {fmtAgo(c.at)}</span>
        <span className="row" style={{ gap: 4, minWidth: 0 }}>{it.room ? <Home size={12} /> : <CamIcon size={12} />}{placeOf(it)}</span>
      </div>
    </div>
  );
}

function Column({ title, icon, color, items, empty, onPick, grid }: {
  title: string; icon: ReactNode; color: string; items: InventoryChange[]; empty: string; onPick: (it: InventoryItem) => void; grid?: boolean;
}) {
  return (
    <section style={{ minWidth: 0 }}>
      <div className="section-title row" style={{ gap: 8, color }}>
        {icon}{title}
        <span className="badge outline" style={{ textTransform: "none", letterSpacing: 0 }}>{items.length}</span>
      </div>
      {items.length === 0 ? <div className="muted" style={{ fontSize: 13, padding: "6px 2px" }}>{empty}</div> : (
        <div style={grid ? { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(270px, 1fr))", gap: 10 } : { display: "flex", flexDirection: "column", gap: 10 }}>
          {items.map((c, i) => <ChangeCard key={`${c.type}-${c.item.id}-${i}`} c={c} color={color} onPick={onPick} />)}
        </div>
      )}
    </section>
  );
}

/** Reiter „Veränderungen“ der Seite „Gegenstände“ (B2): Was ist seit … neu, weg, verschoben? */
export function ObjectChanges({ cams, rooms, onPick }: { cams: Camera[]; rooms: string[]; onPick: (it: InventoryItem) => void }) {
  const [sp, setSp] = useSearchParams();
  const f = {
    period: sp.get("period") ?? "morning",
    from: sp.get("from") ?? "",
    to: sp.get("to") ?? "",
    camera: sp.get("camera") ?? "",
    room: sp.get("room") ?? "",
    transient: sp.get("transient") === "1",
  };
  const setF = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v); else n.delete(k);
    setSp(n, { replace: true });
  };
  const custom = f.period === "custom";
  const since = custom ? localToIso(f.from) : f.period;
  const until = custom ? localToIso(f.to) : undefined;

  const q = useQuery({
    queryKey: ["inventory", "changes", since, until, f.camera, f.room, f.transient],
    enabled: !!since,
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
    queryFn: () => api.inventoryChanges({
      since: since!, until, camera_id: f.camera ? Number(f.camera) : undefined, room: f.room || undefined,
      include_transient: f.transient || undefined, limit: 300,
    }),
  });
  const by = useMemo(() => {
    const m: Record<InventoryChangeType, InventoryChange[]> = { appeared: [], disappeared: [], moved: [], uncertain: [], transient: [] };
    for (const c of q.data?.changes ?? []) m[c.type].push(c);
    return m;
  }, [q.data]);
  const unclearCams = [...new Set(by.uncertain.map((c) => c.item.camera_name))];
  const shown = (q.data?.changes.length ?? 0);
  const total = q.data ? Object.values(q.data.counts).reduce((a, b) => a + b, 0) : 0;

  return (
    <>
      <div className="card filters">
        <div className="field"><label>Zeitraum</label>
          <div className="seg">
            {PERIODS.map((p) => (
              <button key={p.k} className={f.period === p.k ? "on" : ""} onClick={() => setF("period", p.k === "morning" ? "" : p.k)}>{p.label}</button>
            ))}
          </div>
        </div>
        {custom && <>
          <div className="field"><label>Von</label><input type="datetime-local" className="input input-sm" value={f.from} onChange={(e) => setF("from", e.target.value)} /></div>
          <div className="field"><label>Bis (leer = jetzt)</label><input type="datetime-local" className="input input-sm" value={f.to} onChange={(e) => setF("to", e.target.value)} /></div>
        </>}
        <div className="field"><label>Kamera</label>
          <select className="select input-sm" value={f.camera} onChange={(e) => setF("camera", e.target.value)}>
            <option value="">Alle Kameras</option>
            {cams.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
        <div className="field"><label>Raum</label>
          <select className="select input-sm" value={f.room} onChange={(e) => setF("room", e.target.value)}>
            <option value="">Alle Räume</option>
            {rooms.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
        <label className="row" style={{ gap: 6, fontSize: 13, alignSelf: "flex-end", paddingBottom: 6, cursor: "pointer" }}
          title="Gegenstände, die im Zeitraum auftauchten und wieder verschwanden (auch kurz gezeigte)">
          <input type="checkbox" checked={f.transient} onChange={(e) => setF("transient", e.target.checked ? "1" : "")} />
          Zwischendurch Sichtbares zeigen
        </label>
      </div>

      {custom && !since ? <Empty icon={<Clock size={44} />} title="Zeitraum wählen" text="Bitte einen Beginn („Von“) angeben." />
        : q.isLoading ? <Loading />
        : q.isError ? <Empty title="Fehler beim Laden" text={(q.error as Error).message} />
        : q.data && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div className="card card-pad row" style={{ alignItems: "flex-start", borderColor: "color-mix(in srgb, var(--accent) 35%, var(--line))" }}>
              <MessageSquareText size={20} color="var(--accent)" style={{ flex: "none", marginTop: 1 }} />
              <div>
                <div style={{ fontWeight: 600 }}>{q.data.summary}</div>
                {shown < total && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Angezeigt: {shown} von {total} Veränderungen</div>}
              </div>
            </div>

            {by.uncertain.length > 0 && (
              <div className="card card-pad row" style={{ alignItems: "flex-start", borderColor: "color-mix(in srgb, var(--warn) 55%, transparent)" }}>
                <TriangleAlert size={18} color="var(--warn)" style={{ flex: "none", marginTop: 1 }} />
                <div style={{ fontSize: 13 }}>
                  <div style={{ fontWeight: 600, color: "var(--warn)" }}>Stand unklar bei {by.uncertain.length} {by.uncertain.length === 1 ? "Gegenstand" : "Gegenständen"}</div>
                  <div className="dim" style={{ marginTop: 2 }}>
                    {unclearCams.length === 1 ? `Kamera ${unclearCams[0]} liefert` : `Die Kameras ${unclearCams.join(", ")} liefern`} gerade keine Gegenstandsanalyse
                    (Kamera offline, Erkennung aus oder Worker neu gestartet). Ob sich dort etwas verändert hat, ist nicht bekannt:
                    {" "}{by.uncertain.slice(0, 8).map((c) => c.item.name_de).join(", ")}{by.uncertain.length > 8 ? " …" : ""}
                  </div>
                </div>
              </div>
            )}

            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(270px, 1fr))", gap: 18, alignItems: "start" }}>
              {COLS.map((col) => <Column key={col.type} {...col} items={by[col.type]} onPick={onPick} />)}
            </div>

            {f.transient && (
              <Column title="Zwischendurch da" icon={<Timer size={15} />} color="var(--text-2)" items={by.transient} onPick={onPick} grid
                empty="Nichts, das nur zwischendurch zu sehen war." />
            )}
          </div>
        )}
    </>
  );
}
