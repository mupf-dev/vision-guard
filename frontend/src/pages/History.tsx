import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Clock, Search } from "lucide-react";
import { api } from "../lib/api";
import { Empty, Loading } from "../components/ui";

/**
 * Seite „Zeitmaschine“ (W3, Platzhalter aus K3 – Umsetzung B1): Textsuche in der Vergangenheit („rotes Paket im
 * Flur“) → Zeiträume je Kamera mit Vorschau; Klick → Filmstreifen ±10 min. Daten: api.historySearch(),
 * api.historyFrames(), api.historyStats().
 */
export function HistoryPage() {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const stats = useQuery({ queryKey: ["history", "stats"], queryFn: api.historyStats });
  const res = useQuery({ queryKey: ["history", "search", query], queryFn: () => api.historySearch({ q: query }), enabled: !!query, retry: false });
  return (
    <div className="page">
      <div className="page-header">
        <div><h1>Zeitmaschine</h1><div className="sub">Was war wann zu sehen? Suche in Bildbeschreibungen der Vergangenheit</div></div>
      </div>
      <form className="row-wrap" style={{ gap: 8, marginBottom: 16 }} onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()); }}>
        <input className="input" style={{ flex: 1, minWidth: 220 }} placeholder="z. B. roter Karton im Flur" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn btn-primary" type="submit" disabled={!q.trim()}><Search size={15} />Suchen</button>
      </form>
      {stats.data && !stats.data.enabled ? (
        <Empty icon={<Clock size={44} />} title="Zeitmaschine nicht verfügbar" text="Die Datenbank hat keine Vektorsuche (pgvector)." />
      ) : res.isLoading ? <Loading /> : res.isError ? (
        <Empty icon={<Clock size={44} />} title="Suche nicht möglich" text={(res.error as Error).message} />
      ) : res.data?.items.length ? (
        <div className="card"><div className="card-body">
          {res.data.items.map((e, i) => (
            <div key={i}>{e.camera_name}: {new Date(e.start_ts).toLocaleString("de-DE")} – {new Date(e.end_ts).toLocaleTimeString("de-DE")} · {e.score} %</div>
          ))}
        </div></div>
      ) : (
        <Empty icon={<Clock size={44} />} title={query ? "Nichts gefunden" : "Noch keine Suche"}
          text={`Aktivieren Sie die Zeitmaschine je Kamera in der Kameraverwaltung. Gespeichert: ${stats.data?.frames ?? 0} Bilder.`} />
      )}
    </div>
  );
}
