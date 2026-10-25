import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, } from "recharts";
import { Activity, Cctv, Fingerprint, Table2, TrendingUp } from "lucide-react";
import { api } from "../lib/api";
import { labelName } from "../lib/labels";
import { parseTs } from "../lib/format";
import { Empty, Loading } from "../components/ui";

/* Kategoriale Chart-Palette (validiert für dunkle Flächen), feste Reihenfolge – Farbe folgt dem Label, nie dem Rang. */
const SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
const LABEL_ORDER = ["person", "car", "dog", "cat", "truck", "bicycle", "bus", "motorcycle"];
const seriesColor = (l: string) => {
  const i = LABEL_ORDER.indexOf(l);
  return i >= 0 ? SERIES[i] : "#8a94a6";
};
const AXIS = { stroke: "#6b7a90", fontSize: 12 };

interface TipEntry { dataKey?: unknown; value?: unknown; color?: string }
function ChartTip({ active, payload, label, title }: { active?: boolean; payload?: readonly TipEntry[]; label?: unknown; title?: (l: unknown) => string }) {
  if (!active || !payload?.length) return null;
  const rows = payload.filter((p) => Number(p.value) > 0);
  const sum = rows.reduce((a, p) => a + Number(p.value), 0);
  return (
    <div className="chart-tooltip">
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{title ? title(label) : String(label)}</div>
      {rows.map((p) => (
        <div className="r" key={String(p.dataKey)}><span className="row" style={{ gap: 6 }}><i style={{ background: p.color }} />{labelName(String(p.dataKey))}</span><b>{String(p.value)}</b></div>
      ))}
      {rows.length > 1 && <div className="r" style={{ borderTop: "1px solid var(--line)", marginTop: 4, paddingTop: 4 }}><span>Summe</span><b>{sum}</b></div>}
    </div>
  );
}

export function StatsPage() {
  const [hours, setHours] = useState(24);
  const [table, setTable] = useState(false);
  const stats = useQuery({ queryKey: ["stats", hours], queryFn: () => api.stats(hours), refetchInterval: 60_000 });
  const cams = useQuery({ queryKey: ["cameras"], queryFn: api.cameras });

  const { rows, labels } = useMemo(() => {
    const tl = stats.data?.timeline ?? [];
    const labelSet = new Set(tl.map((t) => t.label));
    const byTs = new Map<string, Record<string, number | string>>();
    for (const t of tl) {
      const r = byTs.get(t.ts) ?? { ts: t.ts };
      r[t.label] = ((r[t.label] as number) ?? 0) + t.count;
      byTs.set(t.ts, r);
    }
    const labels = [...labelSet].sort((a, b) => {
      const ia = LABEL_ORDER.indexOf(a), ib = LABEL_ORDER.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    const rows = [...byTs.values()].sort((a, b) => (String(a.ts) < String(b.ts) ? -1 : 1));
    for (const r of rows) for (const l of labels) r[l] ??= 0;
    return { rows, labels };
  }, [stats.data]);

  const camRows = useMemo(() => Object.entries(stats.data?.by_camera ?? {})
    .map(([id, n]) => ({ name: cams.data?.find((c) => String(c.id) === id)?.name ?? `Gelöschte Kamera (ID ${id})`, count: n }))
    .sort((a, b) => b.count - a.count), [stats.data, cams.data]);

  const total = Object.values(stats.data?.by_label ?? {}).reduce((a, b) => a + b, 0);
  const top = Object.entries(stats.data?.by_label ?? {}).sort((a, b) => b[1] - a[1])[0];
  const fmtHour = (v: unknown) => {
    const d = parseTs(String(v));
    if (!d) return "";
    return hours > 48 ? d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" }) + " " + d.toLocaleTimeString("de-DE", { hour: "2-digit" }) + " h"
      : d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  };

  return (
    <div className="page">
      <div className="page-header">
        <div><h1>Statistik</h1><div className="sub">Erkennungen und neue Identitäten im gewählten Zeitraum</div></div>
        <div className="row">
          <button className={`btn btn-sm ${table ? "active" : ""}`} onClick={() => setTable(!table)}><Table2 size={14} />Tabelle</button>
          <div className="seg">
            {[6, 24, 72, 168].map((h) => <button key={h} className={hours === h ? "on" : ""} onClick={() => setHours(h)}>{h < 48 ? `${h} h` : `${h / 24} Tage`}</button>)}
          </div>
        </div>
      </div>
      {stats.isLoading ? <Loading /> : stats.isError ? <Empty title="Statistik nicht verfügbar" text={(stats.error as Error).message} /> : (
        <>
          <div className="stat-tiles">
            <div className="card stat-tile"><div className="l"><Activity size={14} />Erkennungen</div><div className="v">{total.toLocaleString("de-DE")}</div></div>
            <div className="card stat-tile"><div className="l"><TrendingUp size={14} />Häufigstes Objekt</div><div className="v">{top ? labelName(top[0]) : "–"}</div><div className="muted">{top ? `${top[1]}×` : ""}</div></div>
            <div className="card stat-tile"><div className="l"><Cctv size={14} />Aktivste Kamera</div><div className="v">{camRows[0]?.name ?? "–"}</div><div className="muted">{camRows[0] ? `${camRows[0].count}×` : ""}</div></div>
            <div className="card stat-tile"><div className="l"><Fingerprint size={14} />Neue Identitäten</div><div className="v">{stats.data?.identities_new ?? 0}</div></div>
          </div>

          <div className="card" style={{ marginBottom: 16 }}>
            <div className="card-head"><h3>Erkennungen pro Stunde nach Objekt</h3></div>
            <div className="card-body">
              <div className="legend">{labels.map((l) => <span key={l}><i style={{ background: seriesColor(l) }} />{labelName(l)} <b>{stats.data?.by_label[l] ?? 0}</b></span>)}</div>
              {rows.length === 0 ? <Empty title="Keine Daten" /> : (
                <div className="chart-box">
                  <ResponsiveContainer>
                    <BarChart data={rows} margin={{ top: 8, right: 8, left: -16, bottom: 0 }} barCategoryGap="18%">
                      <CartesianGrid vertical={false} stroke="#1f2a3a" />
                      <XAxis dataKey="ts" tickFormatter={fmtHour} tick={AXIS} axisLine={false} tickLine={false} minTickGap={24} />
                      <YAxis tick={AXIS} axisLine={false} tickLine={false} allowDecimals={false} />
                      <Tooltip cursor={{ fill: "rgba(255,255,255,0.04)" }} content={(p) => <ChartTip active={p.active} payload={p.payload as readonly TipEntry[]} label={p.label} title={fmtHour} />} />
                      {labels.map((l, i) => (
                        <Bar key={l} dataKey={l} stackId="a" fill={seriesColor(l)} stroke="#0f1620" strokeWidth={2}
                          radius={i === labels.length - 1 ? [4, 4, 0, 0] : 0} isAnimationActive={false} />
                      ))}
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>
          </div>

          <div className="grid-2">
            <div className="card">
              <div className="card-head"><h3>Erkennungen pro Kamera</h3></div>
              <div className="card-body">
                {camRows.length === 0 ? <Empty title="Keine Daten" /> : (
                  <div style={{ height: Math.max(120, camRows.length * 44) }}>
                    <ResponsiveContainer>
                      <BarChart data={camRows} layout="vertical" margin={{ top: 0, right: 40, left: 0, bottom: 0 }}>
                        <XAxis type="number" hide />
                        <YAxis type="category" dataKey="name" tick={{ ...AXIS, fill: "#a3b0c2" }} axisLine={false} tickLine={false} width={110} />
                        <Tooltip cursor={{ fill: "rgba(255,255,255,0.04)" }} content={({ active, payload }) => active && payload?.length ? (
                          <div className="chart-tooltip"><b>{payload[0].payload.name}</b>: {payload[0].value}</div>) : null} />
                        <Bar dataKey="count" fill="#3987e5" radius={[0, 4, 4, 0]} barSize={18} isAnimationActive={false}
                          label={{ position: "right", fill: "#a3b0c2", fontSize: 12 }} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </div>
            </div>
            <div className="card">
              <div className="card-head"><h3>Nach Objekt</h3></div>
              <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {Object.entries(stats.data?.by_label ?? {}).sort((a, b) => b[1] - a[1]).map(([l, n]) => (
                  <div key={l}>
                    <div className="row" style={{ justifyContent: "space-between", fontSize: 13, marginBottom: 4 }}>
                      <span className="row" style={{ gap: 6 }}><i style={{ width: 10, height: 10, borderRadius: 3, background: seriesColor(l), display: "inline-block" }} />{labelName(l)}</span>
                      <b>{n} <span className="muted" style={{ fontWeight: 400 }}>({total ? Math.round((n / total) * 100) : 0} %)</span></b>
                    </div>
                    <div style={{ height: 6, background: "var(--bg-4)", borderRadius: 4 }}>
                      <div style={{ width: `${total ? (n / total) * 100 : 0}%`, height: "100%", background: seriesColor(l), borderRadius: 4, transition: "width .5s" }} />
                    </div>
                  </div>
                ))}
                {total === 0 && <Empty title="Keine Daten" />}
              </div>
            </div>
          </div>

          {table && (
            <div className="card" style={{ marginTop: 16, overflow: "auto", maxHeight: 420 }}>
              <table className="table">
                <thead><tr><th>Zeit</th>{labels.map((l) => <th key={l}>{labelName(l)}</th>)}</tr></thead>
                <tbody>{rows.map((r) => <tr key={String(r.ts)}><td className="mono">{fmtHour(r.ts)}</td>{labels.map((l) => <td key={l}>{r[l]}</td>)}</tr>)}</tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
