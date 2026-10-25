import { useState } from "react";
import { Link } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Package, Plus, ShoppingCart, Trash2, Wand2 } from "lucide-react";
import { api } from "../lib/api";
import type { ShoppingItem, VirtualSensor } from "../lib/types";
import { Empty, Loading } from "../components/ui";
import { RegionImage, SENSOR_KIND, SensorStateBadge, fmtSensorValue, vlmMeasure } from "../components/SensorBits";
import { fmtAgo } from "../lib/format";
import { toast } from "../lib/store";

/**
 * Seite „Vorräte“ (W3, C4): Vorrats-Sensoren (Ampel ok/knapp/leer, Füllbalken) und die Einkaufsliste zum Abhaken
 * und Ergänzen. WS `sensor`/`shopping_list` aktualisieren die Daten (useLiveSocket).
 */

function fillShare(s: VirtualSensor): number | null {
  if (s.value === null) return null;
  if (s.kind === "fill") return Math.max(0, Math.min(1, s.value));
  const m = vlmMeasure(s);
  if (s.kind === "inventory_count" || m === "count") {
    const min = Number((s.params as { min_count?: number }).min_count ?? 1);
    return Math.max(0, Math.min(1, s.value / Math.max(1, min * 1.5)));
  }
  if (m === "fill") return Math.max(0, Math.min(1, s.value));   // W4: Bildmodell (nur leer/nicht leer verlässlich)
  return null;
}

function SupplyCard({ s }: { s: VirtualSensor }) {
  const share = s.state === "unknown" || !s.enabled ? null : fillShare(s);
  const cls = s.state === "low" ? "low" : s.state === "out" ? "out" : "";
  return (
    <div className={`supply-card ${cls}`}>
      <div className="media">
        {s.snapshot_url ? <img src={s.snapshot_url} alt="" loading="lazy" /> : <RegionImage src={null} region={s.region} height={130} />}
        <div className="corner"><SensorStateBadge s={s} /></div>
      </div>
      <div className="body">
        <b>{s.name}</b>
        {share !== null && <div className={`fill-bar ${cls}`}><span style={{ width: `${Math.round(share * 100)}%` }} /></div>}
        <div className="muted" style={{ fontSize: 12 }}>
          {s.state_text} · {fmtSensorValue(s)}<br />
          {SENSOR_KIND[s.kind].name} · {s.room ?? s.camera_name}{s.last_eval ? ` · geprüft ${fmtAgo(s.last_eval)}` : ""}
        </div>
      </div>
    </div>
  );
}

const SOURCE_ICON: Record<ShoppingItem["source"], React.ReactNode> = {
  auto: <span title="automatisch (Vorrat knapp/leer)"><Wand2 size={13} /></span>,
  manual: null,
  mcp: <span title="vom KI-Assistenten (MCP)"><Bot size={13} /></span>,
};

export function SuppliesPage() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [showDone, setShowDone] = useState(false);
  const sensors = useQuery({ queryKey: ["sensors", "supply"], queryFn: () => api.sensors({ supply: true }), refetchInterval: 30000 });
  const list = useQuery({ queryKey: ["shopping", showDone], queryFn: () => api.shoppingList(showDone ? "all" : "false") });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const refresh = () => qc.invalidateQueries({ queryKey: ["shopping"] });
  const add = useMutation({
    mutationFn: () => api.addShoppingItem(name.trim()),
    onSuccess: () => { setName(""); refresh(); },
    onError: (e: Error) => toast.error("Nicht hinzugefügt", e.message),
  });
  const upd = useMutation({
    mutationFn: ({ id, done }: { id: number; done: boolean }) => api.updateShoppingItem(id, { done }),
    onSuccess: refresh,
    onError: (e: Error) => toast.error("Nicht gespeichert", e.message),
  });
  const del = useMutation({
    mutationFn: (id: number) => api.deleteShoppingItem(id),
    onSuccess: refresh,
    onError: (e: Error) => toast.error("Nicht gelöscht", e.message),
  });
  if (sensors.isLoading || list.isLoading) return <Loading />;
  const items = list.data?.items ?? [];
  const supplies = [...(sensors.data ?? [])].sort((a, b) => {
    const rank = { out: 0, low: 1, unknown: 2, ok: 3, on: 3, off: 3 } as const;
    return rank[a.state] - rank[b.state] || a.name.localeCompare(b.name);
  });
  return (
    <div className="page">
      <div className="page-header">
        <div><h1>Vorräte</h1><div className="sub">Vorräte im Blick und die Einkaufsliste dazu</div></div>
      </div>
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head"><ShoppingCart size={18} color="var(--accent)" /><h3>Einkaufsliste</h3><span className="badge">{list.data?.open_count ?? 0} offen</span>
          <span className="spacer" />
          <label className="row muted" style={{ gap: 6, fontSize: 13, cursor: "pointer" }}>
            <input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} />erledigte zeigen
          </label>
        </div>
        <div className="card-body">
          <form className="row-wrap" style={{ gap: 8, marginBottom: 12 }} onSubmit={(e) => { e.preventDefault(); if (name.trim()) add.mutate(); }}>
            <input className="input" style={{ flex: 1, minWidth: 200 }} placeholder="Artikel hinzufügen" maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
            <button className="btn" type="submit" disabled={!name.trim() || add.isPending}><Plus size={15} />Hinzufügen</button>
          </form>
          {!items.length ? <span className="muted">Die Liste ist leer.</span> : items.map((i) => (
            <div key={i.id} className={`shop-row ${i.done ? "done" : ""}`}>
              <input type="checkbox" checked={i.done} onChange={(e) => upd.mutate({ id: i.id, done: e.target.checked })} title={i.done ? "wieder offen" : "erledigt"} />
              <span className="name" style={{ flex: 1, minWidth: 0 }}>{i.name}{i.note ? <span className="muted"> – {i.note}</span> : null}</span>
              <span className="muted row" style={{ gap: 6, fontSize: 12 }}>{SOURCE_ICON[i.source]}{fmtAgo(i.done_at ?? i.created_at)}</span>
              <button className="btn btn-ghost btn-icon btn-sm" title="Entfernen" onClick={() => del.mutate(i.id)}><Trash2 size={14} /></button>
            </div>
          ))}
          {settings.data && !settings.data.shopping_list_auto && (
            <div className="hint muted" style={{ fontSize: 12, marginTop: 8 }}>Automatische Einträge sind ausgeschaltet (Einstellungen → Sensoren und Vorräte).</div>
          )}
        </div>
      </div>
      {!supplies.length ? (
        <div className="card"><Empty icon={<Package size={44} />} title="Noch keine Vorräte"
          text={<>Legen Sie unter <Link to="/admin/sensoren">Administration → Sensoren</Link> einen Sensor mit „Vorrat“ an – z. B. Flaschen im
            Regal zählen, den Füllstand einer Obstschale mit Referenzbildern „voll/halb/leer“ vergleichen oder das Bildmodell kleine Mengen
            zählen lassen („How many eggs?“). Knappe Vorräte landen dann automatisch auf der Einkaufsliste.</>} /></div>
      ) : (
        <div className="supply-grid">{supplies.map((s) => <SupplyCard key={s.id} s={s} />)}</div>
      )}
      <div className="hint muted" style={{ fontSize: 12, marginTop: 12 }}>
        Die Messung ist eine Schätzung: verdeckte hintere Reihen, Spiegelungen und unbekannte Verpackungen können das Ergebnis verfälschen.
        Steht eine Person im Bereich, wird nicht gemessen.
      </div>
    </div>
  );
}
