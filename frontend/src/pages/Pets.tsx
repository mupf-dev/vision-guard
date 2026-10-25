import { useQuery } from "@tanstack/react-query";
import { PawPrint } from "lucide-react";
import { api } from "../lib/api";
import { Empty, Loading } from "../components/ui";

/**
 * Seite „Tiere“ – Haustier-Tagebuch (W2, Platzhalter aus K2 – Umsetzung C3): je Tier Napf/Wasser/Klo heute vs.
 * normal, 14-Tage-Verlauf, Besuchsliste. Daten: api.petSummary(), api.petVisits().
 */
export function PetsPage() {
  const q = useQuery({ queryKey: ["pets", "summary"], queryFn: () => api.petSummary(14) });
  if (q.isLoading) return <Loading />;
  return (
    <div className="page">
      <div className="page-header">
        <div><h1>Tiere</h1><div className="sub">Haustier-Tagebuch: Napf, Wasser und Katzenklo</div></div>
      </div>
      {!q.data?.length ? (
        <Empty icon={<PawPrint size={44} />} title="Noch keine Besuche"
          text="Legen Sie in der Kameraverwaltung Zonen mit dem Zweck „Napf“, „Wasser“ oder „Klo“ an." />
      ) : (
        <div className="card"><div className="card-body">
          {q.data.map((p, i) => (
            <div key={p.identity?.id ?? `${p.label}-${i}`}>{p.identity?.display_name ?? p.label}: {Object.entries(p.purposes).map(([k, v]) => `${k} ${v?.today ?? 0}`).join(" · ")}</div>
          ))}
        </div></div>
      )}
    </div>
  );
}
