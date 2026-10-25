import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, ShoppingCart } from "lucide-react";
import { api } from "../lib/api";
import { Empty, Loading } from "../components/ui";
import { toast } from "../lib/store";

/**
 * Seite „Vorräte“ (W3, Platzhalter aus K3 – Umsetzung A3+C4): Vorrats-Sensoren (Ampel ok/knapp/leer, Füllbalken)
 * und Einkaufsliste zum Abhaken/Ergänzen. Daten: api.sensors({ supply: true }), api.shoppingList(),
 * api.addShoppingItem(), api.updateShoppingItem(). WS `sensor`/`shopping_list` aktualisieren.
 */
export function SuppliesPage() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const sensors = useQuery({ queryKey: ["sensors", "supply"], queryFn: () => api.sensors({ supply: true }) });
  const list = useQuery({ queryKey: ["shopping"], queryFn: () => api.shoppingList() });
  const add = useMutation({
    mutationFn: () => api.addShoppingItem(name.trim()),
    onSuccess: () => { setName(""); qc.invalidateQueries({ queryKey: ["shopping"] }); },
    onError: (e: Error) => toast.error("Nicht hinzugefügt", e.message),
  });
  if (sensors.isLoading || list.isLoading) return <Loading />;
  return (
    <div className="page">
      <div className="page-header">
        <div><h1>Vorräte</h1><div className="sub">Vorräte im Blick und die Einkaufsliste dazu</div></div>
      </div>
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-head"><ShoppingCart size={18} color="var(--accent)" /><h3>Einkaufsliste</h3><span className="badge">{list.data?.open_count ?? 0}</span></div>
        <div className="card-body">
          <form className="row-wrap" style={{ gap: 8, marginBottom: 12 }} onSubmit={(e) => { e.preventDefault(); if (name.trim()) add.mutate(); }}>
            <input className="input" style={{ flex: 1, minWidth: 200 }} placeholder="Artikel hinzufügen" value={name} onChange={(e) => setName(e.target.value)} />
            <button className="btn" type="submit" disabled={!name.trim() || add.isPending}><Plus size={15} />Hinzufügen</button>
          </form>
          {list.data?.items.length ? list.data.items.map((i) => <div key={i.id}>{i.name}{i.note ? ` – ${i.note}` : ""}</div>)
            : <span className="muted">Die Liste ist leer.</span>}
        </div>
      </div>
      {!sensors.data?.length ? (
        <Empty icon={<ShoppingCart size={44} />} title="Noch keine Vorräte"
          text="Legen Sie unter Administration → Sensoren einen Sensor mit „Vorrat“ an (z. B. Wasserflaschen zählen)." />
      ) : (
        <div className="card"><div className="card-body">
          {sensors.data.map((s) => <div key={s.id}>{s.name}: {s.state_text || s.state}</div>)}
        </div></div>
      )}
    </div>
  );
}
