import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BadgeCheck, Camera as CamIcon, Clock, History, Home, MapPin, MessageSquareText, MonitorPlay, Package, RotateCcw, Search, Trash2,
} from "lucide-react";
import { api } from "../lib/api";
import type { InventoryItem, InventorySearchHit, InventoryState } from "../lib/types";
import { fmtAgo, fmtDateTime, fmtTime } from "../lib/format";
import { ConfirmDialog, Dialog, Empty, Img, Loading } from "../components/ui";
import { toast, useLive } from "../lib/store";
import { ObjectChanges } from "./ObjectChanges";
import { MyItems } from "./MyItems";
import { ItemTeachDialog } from "../components/ItemTeachDialog";

const STATE_NAME: Record<InventoryState, string> = { present: "sichtbar", missing: "nicht mehr gesehen", stale: "unbestätigt" };
const STATE_HINT: Record<InventoryState, string> = {
  present: "Gegenstand ist im aktuellen Kamerabild erkannt",
  missing: "Gegenstand wurde zuletzt mehrfach nicht mehr gefunden",
  stale: "Kamera liefert gerade keine Gegenstandserkennung – Stand unbekannt",
};
const FLASH_MS = 6000;

export function StateBadge({ state }: { state: InventoryState }) {
  const cls = state === "present" ? "ok" : state === "missing" ? "warn" : "outline";
  return <span className={`badge ${cls}`} title={STATE_HINT[state]}>{state === "present" && <span className="dot" />}{STATE_NAME[state]}</span>;
}

/** Zeile „aktuell sichtbar …“ bzw. „zuletzt gesehen vor …“ */
function seenText(it: InventoryItem): string {
  if (it.visible) return `aktuell sichtbar · seit ${fmtTime(it.first_seen)}`;
  return `zuletzt gesehen ${fmtAgo(it.last_seen)}`;
}

/** Nach Minuten neu rendern, damit „vor … min“ aktuell bleibt. */
function useTick(ms = 30_000) {
  const [, setT] = useState(0);
  useEffect(() => {
    const t = window.setInterval(() => setT((x) => x + 1), ms);
    return () => window.clearInterval(t);
  }, [ms]);
}

/** Query-Ergebnisse mit neueren Live-Fassungen aus dem WebSocket überlagern. */
function useLiveOverlay<T extends InventoryItem>(items: T[]): T[] {
  const live = useLive((s) => s.inventory);
  const deleted = useLive((s) => s.inventoryDeleted);
  return useMemo(
    () => items
      .filter((i) => !deleted[i.id])
      .map((i) => {
        const l = live[i.id];
        return l && l.updated_at > i.updated_at ? { ...i, ...l } : i;
      }),
    [items, live, deleted],
  );
}

function ItemCard({ it, index, onPick }: { it: InventoryItem; index: number; onPick: (it: InventoryItem) => void }) {
  const flashTs = useLive((s) => s.inventoryFlash[it.id]);
  const flash = flashTs !== undefined && Date.now() - flashTs < FLASH_MS;
  const lc = it.visible ? "var(--accent)" : "var(--line-2)";
  // Raum nur zeigen, wenn er sich vom Kameranamen unterscheidet
  const place = [it.room && it.room !== it.camera_name ? it.room : null, ...it.zone_names].filter(Boolean).join(" · ");
  return (
    <div className="ev-card" role="button" tabIndex={0}
      style={{
        ["--lc" as string]: lc, animationDelay: `${Math.min(index, 20) * 18}ms`, opacity: it.visible ? 1 : 0.82,
        ...(flash ? { borderColor: "var(--accent)", boxShadow: "0 0 0 2px var(--accent-soft)" } : {}),
      }}
      onClick={() => onPick(it)} onKeyDown={(k) => k.key === "Enter" && onPick(it)}>
      <div className="media" style={{ aspectRatio: "4 / 3" }}>
        <Img src={it.crop_url} alt={it.name_de}
          fallback={<div style={{ height: "100%", display: "grid", placeItems: "center", color: "var(--text-3)" }}><Package size={40} /></div>} />
        <div className="corner">
          <StateBadge state={it.state} />
          <span className="spacer" />
          <span className="badge" title="Konfidenz">{Math.round(it.score * 100)} %</span>
        </div>
      </div>
      <div className="body">
        <div className="who"><Package size={15} color={it.visible ? "var(--accent)" : "var(--text-3)"} />{it.identity?.display_name ?? it.name_de}
          {it.identity && <span className="badge outline" title={`eigener Gegenstand (${it.name_de})`} style={{ marginLeft: 6, textTransform: "none", letterSpacing: 0 }}><BadgeCheck size={11} />{it.name_de}</span>}
        </div>
        <div className="meta">
          <span className="row" style={{ gap: 4, minWidth: 0 }}><CamIcon />{it.camera_name}</span>
          {place && <span className="row" style={{ gap: 4, minWidth: 0 }}><Home />{place}</span>}
        </div>
        {it.relation && <div className="meta" style={{ color: "var(--text-2)" }}><MapPin />{it.relation.text}</div>}
        <div className="meta" style={{ color: it.visible ? "#4ade80" : undefined }}><Clock />{seenText(it)}</div>
      </div>
    </div>
  );
}

function ItemGrid({ items, onPick }: { items: InventoryItem[]; onPick: (it: InventoryItem) => void }) {
  return <div className="ev-grid">{items.map((it, i) => <ItemCard key={it.id} it={it} index={i} onPick={onPick} />)}</div>;
}

/** Übersichtsbild mit markierter Box des Gegenstands */
function SnapshotWithBox({ it }: { it: InventoryItem }) {
  const [x1, y1, x2, y2] = it.bbox;
  if (!it.snapshot_url) return null;
  return (
    <div style={{ position: "relative", borderRadius: 10, overflow: "hidden", background: "var(--bg-4)", lineHeight: 0 }}>
      <img src={it.snapshot_url} alt="Übersichtsbild" style={{ display: "block", width: "100%", height: "auto" }} />
      <div style={{
        position: "absolute", left: `${x1 * 100}%`, top: `${y1 * 100}%`, width: `${(x2 - x1) * 100}%`, height: `${(y2 - y1) * 100}%`,
        border: "2px solid var(--accent)", borderRadius: 4, boxShadow: "0 0 0 9999px rgba(0,0,0,.35)",
      }} />
    </div>
  );
}

function ItemDialog({ item, onClose }: { item: InventoryItem; onClose: () => void }) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [confirm, setConfirm] = useState(false);
  const [teach, setTeach] = useState(false);
  const fresh = useQuery({ queryKey: ["inventory", "item", item.id], queryFn: () => api.inventoryItem(item.id), initialData: item });
  const [it] = useLiveOverlay(fresh.data ? [fresh.data] : [item]);
  const hist = useQuery({ queryKey: ["inventory", "history", item.id], queryFn: () => api.inventoryHistory(item.id, 50) });
  const del = useMutation({
    mutationFn: () => api.deleteInventoryItem(item.id),
    onSuccess: () => {
      toast.success("Gegenstand entfernt", item.name_de);
      useLive.getState().applyInventory({ type: "inventory", camera_id: item.camera_id, action: "delete", items: [item] });
      qc.invalidateQueries({ queryKey: ["inventory"] });
      onClose();
    },
    onError: (e: Error) => toast.error("Entfernen fehlgeschlagen", e.message),
  });
  if (!it) return null;
  return (
    <Dialog size="lg" onClose={onClose} icon={<Package size={18} />} title={it.identity?.display_name ?? it.name_de} footer={<>
      <button className="btn btn-ghost" onClick={() => setConfirm(true)}><Trash2 size={15} />Fehlerkennung entfernen</button>
      <span className="spacer" />
      {!it.identity && it.crop_url && <button className="btn" onClick={() => setTeach(true)}><BadgeCheck size={15} />Als eigenen Gegenstand merken</button>}
      <button className="btn" onClick={() => nav(`/live/${it.camera_id}`)}><MonitorPlay size={15} />Live ansehen</button>
      <button className="btn btn-primary" onClick={onClose}>Schließen</button>
    </>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 16, alignItems: "start" }}>
          {it.snapshot_url ? <SnapshotWithBox it={it} />
            : <Img src={it.crop_url} className="avatar lg" fallback={<div className="avatar lg" style={{ display: "grid", placeItems: "center" }}><Package size={40} /></div>} />}
          <dl className="kv">
            {it.snapshot_url && it.crop_url && <><dt>Ausschnitt</dt><dd><Img src={it.crop_url} className="avatar" /></dd></>}
            {it.identity && <><dt>Exemplar</dt><dd>{it.identity.display_name}{it.identity_score != null && <span className="muted"> · Ähnlichkeit {Math.round(it.identity_score * 100)} %</span>}</dd></>}
            <dt>Status</dt><dd><StateBadge state={it.state} /> <span className="dim" style={{ marginLeft: 6 }}>{seenText(it)}</span></dd>
            <dt>Kamera</dt><dd>{it.camera_name}</dd>
            <dt>Raum</dt><dd>{it.room ?? <span className="muted">–</span>}</dd>
            {it.zone_names.length > 0 && <><dt>Zonen</dt><dd>{it.zone_names.join(", ")}</dd></>}
            <dt>Lage</dt><dd>{it.relation?.text ?? <span className="muted">keine Angabe</span>}</dd>
            <dt>Konfidenz</dt><dd>{Math.round(it.score * 100)} %</dd>
            <dt>Erstmals</dt><dd>{fmtDateTime(it.first_seen)}</dd>
            <dt>Zuletzt</dt><dd>{fmtDateTime(it.last_seen)}</dd>
            <dt>Schlüssel</dt><dd className="mono muted">{it.label}</dd>
          </dl>
        </div>
        <div>
          <div className="section-title"><History size={12} style={{ verticalAlign: -1, marginRight: 6 }} />Verlauf</div>
          {hist.isLoading ? <Loading /> : !hist.data?.length ? <div className="muted">Noch kein Verlauf.</div> : (
            <div className="card" style={{ maxHeight: 260, overflow: "auto" }}>
              <table className="table">
                <thead><tr><th></th><th>Zeit</th><th>Status</th><th>Konfidenz</th></tr></thead>
                <tbody>
                  {hist.data.map((h, i) => (
                    <tr key={`${h.ts}-${i}`}>
                      <td><Img src={h.crop_url} className="thumb" /></td>
                      <td className="mono" style={{ whiteSpace: "nowrap" }}>{fmtDateTime(h.ts)}</td>
                      <td><StateBadge state={h.state} /></td>
                      <td>{Math.round(h.score * 100)} %</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
      {confirm && <ConfirmDialog title="Gegenstand entfernen?" busy={del.isPending} confirmLabel="Entfernen"
        text={<>„{it.name_de}“ wird samt Verlauf gelöscht (z. B. bei einer Fehlerkennung). Wird er erneut erkannt, erscheint er wieder.</>}
        onConfirm={() => del.mutate()} onClose={() => setConfirm(false)} />}
      {teach && <ItemTeachDialog fromItem={it} onClose={() => setTeach(false)} />}
    </Dialog>
  );
}

function useDebounced<T>(v: T, ms = 300): T {
  const [d, setD] = useState(v);
  useEffect(() => {
    const t = window.setTimeout(() => setD(v), ms);
    return () => window.clearTimeout(t);
  }, [v, ms]);
  return d;
}

const NO_ROOM = "Ohne Raum";

export function ObjectsPage() {
  useTick();
  const [sp, setSp] = useSearchParams();
  const [picked, setPicked] = useState<InventoryItem | null>(null);
  const f = {
    q: sp.get("q") ?? "",
    camera: sp.get("camera") ?? "",
    room: sp.get("room") ?? "",
    state: sp.get("state") ?? "",
    label: sp.get("label") ?? "",
  };
  const setF = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v); else n.delete(k);
    setSp(n, { replace: true });
  };
  // Suchfeld lokal halten (flüssiges Tippen), URL entprellt nachziehen
  const [qInput, setQInput] = useState(f.q);
  const qDeb = useDebounced(qInput.trim(), 300);
  useEffect(() => { if (qDeb !== f.q) setF("q", qDeb); }, [qDeb]); // eslint-disable-line react-hooks/exhaustive-deps

  const cams = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });
  const labels = useQuery({ queryKey: ["inventory", "labels"], queryFn: api.inventoryLabels });
  const list = useQuery({
    queryKey: ["inventory", "list", f.camera, f.room, f.state, f.label],
    placeholderData: keepPreviousData,
    enabled: !f.q,
    queryFn: () => api.inventory({ camera_id: f.camera, room: f.room, state: f.state, label: f.label, limit: 500 }),
  });
  const search = useQuery({
    queryKey: ["inventory", "search", f.q],
    placeholderData: keepPreviousData,
    enabled: !!f.q,
    queryFn: () => api.inventorySearch(f.q, 50),
  });

  // Suche liefert ungefilterte Treffer – Filter hier anwenden
  const rawItems: InventoryItem[] = f.q
    ? (search.data?.items ?? []).filter((h: InventorySearchHit) =>
      (!f.camera || String(h.camera_id) === f.camera) && (!f.room || (h.room ?? "") === f.room)
      && (!f.state || f.state.split(",").includes(h.state)) && (!f.label || h.label === f.label))
    : list.data?.items ?? [];
  const items = useLiveOverlay(rawItems);
  const visibleCount = items.filter((i) => i.visible).length;

  const rooms = useMemo(() => {
    const s = new Set<string>();
    for (const c of cams.data ?? []) if (c.room) s.add(c.room);
    for (const i of list.data?.items ?? []) if (i.room) s.add(i.room);
    return [...s].sort((a, b) => a.localeCompare(b, "de"));
  }, [cams.data, list.data]);
  const invCams = (cams.data ?? []).filter((c) => c.inventory_enabled);

  // Gruppierung nach Raum (ohne Suche); bei der Suche zählt die Trefferreihenfolge
  const groups = useMemo(() => {
    if (f.q) return null;
    const m = new Map<string, InventoryItem[]>();
    for (const it of items) {
      const k = it.room || NO_ROOM;
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(it);
    }
    for (const arr of m.values()) arr.sort((a, b) => Number(b.visible) - Number(a.visible) || a.name_de.localeCompare(b.name_de, "de"));
    return [...m.entries()].sort(([a], [b]) => (a === NO_ROOM ? 1 : b === NO_ROOM ? -1 : a.localeCompare(b, "de")));
  }, [items, f.q]);

  const tabParam = sp.get("tab");
  const tab = tabParam === "changes" || tabParam === "mine" ? tabParam : "overview";
  const setTab = (t: string) => {
    const n = new URLSearchParams(sp);
    if (t === "changes" || t === "mine") n.set("tab", t); else n.delete("tab");
    setSp(n, { replace: true });
  };
  const hasFilter = [f.q, f.camera, f.room, f.state, f.label].some(Boolean);
  const q = f.q ? search : list;
  const loading = q.isLoading;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Gegenstände</h1>
          <div className="sub">
            {items.length.toLocaleString("de-DE")} Gegenstände{hasFilter ? " (gefiltert)" : ""} · {visibleCount} aktuell sichtbar
            {invCams.length > 0 && <> · Erkennung aktiv auf {invCams.map((c) => c.name).join(", ")}</>}
          </div>
        </div>
      </div>

      <div className="tabs">
        <button className={tab === "overview" ? "on" : ""} onClick={() => setTab("overview")}><Package size={15} />Übersicht</button>
        <button className={tab === "changes" ? "on" : ""} onClick={() => setTab("changes")}><History size={15} />Veränderungen</button>
        <button className={tab === "mine" ? "on" : ""} onClick={() => setTab("mine")}><BadgeCheck size={15} />Meine Gegenstände</button>
      </div>

      {tab === "mine" ? <MyItems onPickItem={setPicked} /> : tab === "changes" ? <ObjectChanges cams={cams.data ?? []} rooms={rooms} onPick={setPicked} /> : <>
      <div className="card filters">
        <div className="field" style={{ flex: "1 1 280px" }}><label>Suche</label>
          <div className="input-group">
            <Search size={15} />
            <input className="input input-sm" value={qInput} placeholder="Wo ist …? z. B. Fernbedienung, Schlüssel, Brille"
              onChange={(e) => setQInput(e.target.value)} autoFocus={tab === "overview"} />
          </div>
        </div>
        <div className="field"><label>Kamera</label>
          <select className="select input-sm" value={f.camera} onChange={(e) => setF("camera", e.target.value)}>
            <option value="">Alle Kameras</option>
            {cams.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
        <div className="field"><label>Raum</label>
          <select className="select input-sm" value={f.room} onChange={(e) => setF("room", e.target.value)}>
            <option value="">Alle Räume</option>
            {rooms.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
        <div className="field"><label>Gegenstand</label>
          <select className="select input-sm" value={f.label} onChange={(e) => setF("label", e.target.value)}>
            <option value="">Alle Gegenstände</option>
            {labels.data?.filter((l) => l.count_total > 0).map((l) => (
              <option key={l.label} value={l.label}>{l.name_de} ({l.count_present}/{l.count_total})</option>
            ))}
          </select>
        </div>
        <div className="field"><label>Status</label>
          <select className="select input-sm" value={f.state} onChange={(e) => setF("state", e.target.value)}>
            <option value="">Alle</option>
            <option value="present">Aktuell sichtbar</option>
            <option value="missing">Nicht mehr gesehen</option>
            <option value="stale">Unbestätigt</option>
            <option value="missing,stale">Nicht sichtbar</option>
          </select>
        </div>
        {hasFilter && <button className="btn btn-ghost btn-sm" onClick={() => { setQInput(""); setSp(new URLSearchParams(), { replace: true }); }}><RotateCcw size={14} />Zurücksetzen</button>}
      </div>

      {f.q && search.data && (
        <div className="card card-pad row" style={{ marginBottom: 16, alignItems: "flex-start", borderColor: "color-mix(in srgb, var(--accent) 35%, var(--line))" }}>
          <MessageSquareText size={20} color="var(--accent)" style={{ flex: "none", marginTop: 1 }} />
          <div>
            <div style={{ fontWeight: 600 }}>{search.data.answer}</div>
            {search.data.items.length > 1 && <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{search.data.items.length} Treffer</div>}
          </div>
        </div>
      )}

      {loading ? <Loading /> : q.isError ? <Empty title="Fehler beim Laden" text={(q.error as Error).message} />
        : items.length === 0 ? (
          <Empty icon={<Package size={44} />} title={f.q ? "Nichts gefunden" : "Keine Gegenstände"}
            text={f.q || hasFilter ? "Für diese Suche bzw. Filter wurde nichts gefunden."
              : invCams.length ? "Die Gegenstandserkennung läuft – erkannte Gegenstände erscheinen hier nach wenigen Durchläufen."
              : "Aktiviere die Gegenstandserkennung unter Administration → Kameras."} />
        ) : groups ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
            {groups.map(([room, arr]) => (
              <section key={room}>
                <div className="section-title row" style={{ gap: 8 }}>
                  <Home size={13} />{room}
                  <span className="badge outline" style={{ textTransform: "none", letterSpacing: 0 }}>{arr.filter((i) => i.visible).length}/{arr.length} sichtbar</span>
                </div>
                <ItemGrid items={arr} onPick={setPicked} />
              </section>
            ))}
          </div>
        ) : <ItemGrid items={items} onPick={setPicked} />}
      </>}

      {picked && <ItemDialog item={picked} onClose={() => setPicked(null)} />}
    </div>
  );
}
