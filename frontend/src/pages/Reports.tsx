import { useQuery } from "@tanstack/react-query";
import { FileText } from "lucide-react";
import { api } from "../lib/api";
import { fmtDateTime } from "../lib/format";
import { Empty, Loading } from "../components/ui";

/**
 * Seite „Berichte“ – Tagesberichte (W2, Platzhalter aus K2 – Umsetzung B3): Liste nach Datum, Ansicht mit Text und
 * Abschnitten, „Bericht für heute erstellen“. Daten: api.reports(), api.report(id), api.generateReport().
 */
export function ReportsPage() {
  const q = useQuery({ queryKey: ["reports"], queryFn: () => api.reports() });
  if (q.isLoading) return <Loading />;
  return (
    <div className="page">
      <div className="page-header">
        <div><h1>Berichte</h1><div className="sub">Tägliche Zusammenfassung in Sätzen</div></div>
      </div>
      {!q.data?.items.length ? (
        <Empty icon={<FileText size={44} />} title="Noch keine Berichte" text="Den Tagesbericht schalten Sie unter Administration → Einstellungen ein." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {q.data.items.map((r) => (
            <div className="card" key={r.id}><div className="card-body">
              <div className="muted" style={{ fontSize: 13 }}>{fmtDateTime(r.period_end)} · {r.generator}</div>
              <div>{r.text}</div>
            </div></div>
          ))}
        </div>
      )}
    </div>
  );
}
