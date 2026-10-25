import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, Check, CircleHelp, History, Keyboard, SkipForward, X } from "lucide-react";
import { api } from "../lib/api";
import type { ReviewAnswerInput, ReviewRequest, ReviewStatus } from "../lib/types";
import { fmtAgo, fmtDateTime } from "../lib/format";
import { Empty, Img, Loading } from "../components/ui";
import { toast } from "../lib/store";

/**
 * Seite „Bitte bestätigen“ (W2, D2): offene Rückfragen mit Bildausschnitt, Ja/Nein/Überspringen (Tasten J/N/S für die
 * oberste Frage), bei „Nein“ zu einem Gegenstand optional „Was ist es stattdessen?“, Verlauf der Antworten.
 */
const KIND_DE: Record<string, string> = {
  inventory_label: "Gegenstand", inventory_identity: "Eigener Gegenstand", identity_match: "Person / Tier",
};
const REASON_DE: Record<string, string> = {
  uncertain: "knapp unter der Erkennungsschwelle", low_score: "knapp über der Erkennungsschwelle",
  new_label: "erstmals auf dieser Kamera",
};
const STATUS_DE: Record<ReviewStatus, string> = { open: "offen", yes: "Ja", no: "Nein", skipped: "übersprungen", expired: "verfallen" };
const SOURCE_DE: Record<string, string> = { ui: "Oberfläche", push: "Push-Link", mcp: "KI-Agent", api: "API" };

/** Übersichtsbild mit markierter Box */
function Overview({ url, bbox }: { url: string; bbox: [number, number, number, number] | null }) {
  return (
    <div style={{ position: "relative", lineHeight: 0, borderRadius: 8, overflow: "hidden", background: "var(--bg-4)" }}>
      <img src={url} alt="Übersicht" style={{ width: "100%", height: "auto", display: "block" }} />
      {bbox && <div style={{
        position: "absolute", left: `${bbox[0] * 100}%`, top: `${bbox[1] * 100}%`, width: `${(bbox[2] - bbox[0]) * 100}%`,
        height: `${(bbox[3] - bbox[1]) * 100}%`, border: "2px solid var(--accent)", borderRadius: 3,
      }} />}
    </div>
  );
}

function Pictures({ r }: { r: ReviewRequest }) {
  const box = { width: 200, height: 150, borderRadius: 8, overflow: "hidden", background: "var(--bg-4)", flex: "none" } as const;
  if (r.kind === "identity_match") {
    const d = r.data as { source_name?: string; target_name?: string };
    return (
      <div className="row" style={{ gap: 8, alignItems: "center", flex: "none" }}>
        <div style={{ textAlign: "center" }}><div style={{ ...box, width: 130, height: 130 }}><Img src={r.crop_url} className="contain-img" /></div>
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{d.source_name ?? "unbekannt"}</div></div>
        <ArrowRight size={18} color="var(--text-3)" />
        <div style={{ textAlign: "center" }}><div style={{ ...box, width: 130, height: 130 }}><Img src={r.snapshot_url} className="contain-img" /></div>
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{d.target_name ?? "?"}</div></div>
      </div>
    );
  }
  return (
    <div className="row" style={{ gap: 8, alignItems: "flex-start", flex: "none", flexWrap: "wrap" }}>
      <div style={box}><Img src={r.crop_url} className="contain-img" /></div>
      {r.snapshot_url && <div style={{ width: 200 }}><Overview url={r.snapshot_url} bbox={r.bbox} /></div>}
    </div>
  );
}

function ReviewCard({ r, first, busy, labels, onAnswer }: {
  r: ReviewRequest; first: boolean; busy: boolean; labels: { label: string; name_de: string }[];
  onAnswer: (b: ReviewAnswerInput) => void;
}) {
  const [noMode, setNoMode] = useState(false);
  const [correct, setCorrect] = useState("");
  const canCorrect = r.kind === "inventory_label";
  const reason = (r.data as { reason?: string }).reason;
  const small = (r.data as { small?: boolean }).small;
  const no = () => (canCorrect ? setNoMode(true) : onAnswer({ answer: "no" }));

  useEffect(() => {
    if (!first) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (busy || e.ctrlKey || e.metaKey || e.altKey || ["INPUT", "SELECT", "TEXTAREA"].includes(t.tagName)) return;
      const k = e.key.toLowerCase();
      if (k === "escape") setNoMode(false);
      else if (noMode) { if (k === "enter" || k === "n") onAnswer({ answer: "no", correct_label: correct || null }); }
      else if (k === "j") onAnswer({ answer: "yes" });
      else if (k === "n") no();
      else if (k === "s") onAnswer({ answer: "skip" });
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div className="card" style={first ? { borderColor: "color-mix(in srgb, var(--accent) 45%, var(--line))" } : undefined}>
      <div className="card-body" style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
        <Pictures r={r} />
        <div style={{ flex: 1, minWidth: 220 }}>
          <div className="row" style={{ gap: 6, marginBottom: 4 }}>
            <span className="badge outline">{KIND_DE[r.kind] ?? r.kind}</span>
            {reason && <span className="muted" style={{ fontSize: 12 }}>{REASON_DE[reason] ?? reason}</span>}
          </div>
          <div style={{ fontSize: 16, fontWeight: 600 }}>{r.question}</div>
          <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>
            {r.camera_name ? `${r.camera_name} · ` : ""}{fmtAgo(r.created_at)}
            {r.score !== null ? ` · Ähnlichkeit/Konfidenz ${Math.round(r.score * 100)} %` : ""}
            {r.threshold !== null ? ` (Schwelle ${Math.round(r.threshold * 100)} %)` : ""}
          </div>
          {small && <div className="row" style={{ gap: 6, fontSize: 12, color: "#fbbf24", marginTop: 4 }}><AlertTriangle size={13} />Gegenstand erscheint im Bild sehr klein</div>}
          {noMode && (
            <div className="row" style={{ gap: 8, marginTop: 10, flexWrap: "wrap" }}>
              <label className="muted" style={{ fontSize: 13 }}>Was ist es stattdessen?</label>
              <select className="select input-sm" style={{ width: 220 }} value={correct} autoFocus onChange={(e) => setCorrect(e.target.value)}>
                <option value="">– weiß nicht / nichts –</option>
                {labels.filter((l) => l.label !== r.label).map((l) => <option key={l.label} value={l.label}>{l.name_de}</option>)}
              </select>
              <button className="btn btn-sm" disabled={busy} onClick={() => onAnswer({ answer: "no", correct_label: correct || null })}><X size={14} />Nein senden</button>
              <button className="btn btn-ghost btn-sm" onClick={() => setNoMode(false)}>Abbrechen</button>
            </div>
          )}
        </div>
        {!noMode && (
          <div className="row" style={{ gap: 8 }}>
            <button className="btn btn-primary" disabled={busy} onClick={() => onAnswer({ answer: "yes" })} title="Taste J"><Check size={16} />Ja</button>
            <button className="btn" disabled={busy} onClick={no} title="Taste N"><X size={16} />Nein</button>
            <button className="btn btn-ghost" disabled={busy} onClick={() => onAnswer({ answer: "skip" })} title="Taste S"><SkipForward size={16} />Überspringen</button>
          </div>
        )}
      </div>
    </div>
  );
}

function HistoryList() {
  const q = useQuery({ queryKey: ["reviews", "history"], queryFn: () => api.reviews({ status: "yes,no,skipped,expired", limit: 100 }) });
  if (q.isLoading) return <Loading />;
  const items = q.data?.items ?? [];
  if (!items.length) return <Empty icon={<History size={44} />} title="Noch keine Antworten" />;
  return (
    <div className="card" style={{ overflow: "auto" }}>
      <table className="table">
        <thead><tr><th></th><th>Frage</th><th>Antwort</th><th>Kamera</th><th>Zeit</th><th>über</th></tr></thead>
        <tbody>
          {items.map((r) => (
            <tr key={r.id}>
              <td><Img src={r.crop_url} className="thumb" /></td>
              <td>{r.question}{r.correct_label && <div className="muted" style={{ fontSize: 12 }}>stattdessen: {r.correct_label}</div>}</td>
              <td><span className={`badge ${r.status === "yes" ? "ok" : r.status === "no" ? "bad" : "outline"}`}>{STATUS_DE[r.status]}</span></td>
              <td>{r.camera_name ?? "–"}</td>
              <td className="mono" style={{ whiteSpace: "nowrap" }}>{fmtDateTime(r.answered_at ?? r.expires_at ?? r.created_at)}</td>
              <td className="muted">{r.answer_source ? SOURCE_DE[r.answer_source] ?? r.answer_source : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ReviewPage() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<"open" | "history">("open");
  const q = useQuery({ queryKey: ["reviews", "open"], queryFn: () => api.reviews({ status: "open" }) });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const labels = (settings.data?.inventory_vocabulary ?? []).map((v) => ({ label: v.label, name_de: v.name_de }));
  const answer = useMutation({
    mutationFn: ({ id, b }: { id: number; b: ReviewAnswerInput }) => api.answerReview(id, b),
    onSuccess: (r) => {
      toast.success("Antwort gespeichert", `${r.question} – ${STATUS_DE[r.status]}`);
      qc.invalidateQueries({ queryKey: ["reviews"] });
      qc.invalidateQueries({ queryKey: ["inventory"] });
      qc.invalidateQueries({ queryKey: ["identities"] });
    },
    onError: (e: Error) => { toast.error("Antwort nicht gespeichert", e.message); qc.invalidateQueries({ queryKey: ["reviews"] }); },
  });
  const d = q.data;
  return (
    <div className="page">
      <div className="page-header">
        <div><h1>Bitte bestätigen</h1>
          <div className="sub">{d ? `${d.open_count} offen · heute ${d.asked_today}/${d.max_per_day} gefragt` : "Rückfragen der Erkennung"}</div>
        </div>
        <span className="muted row" style={{ gap: 6, fontSize: 12 }}><Keyboard size={14} />J = Ja · N = Nein · S = Überspringen</span>
      </div>
      <div className="tabs">
        <button className={tab === "open" ? "on" : ""} onClick={() => setTab("open")}><CircleHelp size={15} />Offen<span className="count">{d?.open_count ?? 0}</span></button>
        <button className={tab === "history" ? "on" : ""} onClick={() => setTab("history")}><History size={15} />Verlauf</button>
      </div>
      {tab === "history" ? <HistoryList /> : q.isLoading ? <Loading /> : !d?.items.length ? (
        <Empty icon={<CircleHelp size={44} />} title="Keine offenen Fragen"
          text="Bei unsicheren Erkennungen fragt VisionGuard hier nach – Ihre Antworten verbessern die Erkennung (Schwellen, Fehlerkennungen, eigene Gegenstände, Personen)." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {d.items.map((r, i) => <ReviewCard key={r.id} r={r} first={i === 0} labels={labels} busy={answer.isPending}
            onAnswer={(b) => answer.mutate({ id: r.id, b })} />)}
        </div>
      )}
    </div>
  );
}
