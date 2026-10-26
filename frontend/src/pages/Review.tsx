import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, Check, CircleHelp, History, Keyboard, SkipForward, X } from "lucide-react";
import { api } from "../lib/api";
import type { ReviewAnswerInput, ReviewRequest } from "../lib/types";
import { fmtAgo, fmtDateTime } from "../lib/format";
import { Empty, Img, Loading } from "../components/ui";
import { toast } from "../lib/store";
import { lang, useMsgs } from "../i18n";

/**
 * Seite „Bitte bestätigen“ (W2, D2): offene Rückfragen mit Bildausschnitt, Ja/Nein/Überspringen (Tasten J/N/S für die
 * oberste Frage), bei „Nein“ zu einem Gegenstand optional „Was ist es stattdessen?“, Verlauf der Antworten.
 */
/** Anzeigename eines Vokabular-Eintrags in der aktuellen Sprache (CONTRACT I18N.C). */
function vocabDisplayName(v: { name_de: string; names?: Record<string, string>; prompts?: string[] }): string {
  const l = lang();
  return v.names?.[l] ?? (l === "de" ? v.name_de : undefined) ?? v.names?.en ?? v.prompts?.[0] ?? v.name_de;
}

/** Übersichtsbild mit markierter Box */
function Overview({ url, bbox }: { url: string; bbox: [number, number, number, number] | null }) {
  const m = useMsgs();
  return (
    <div style={{ position: "relative", lineHeight: 0, borderRadius: 8, overflow: "hidden", background: "var(--bg-4)" }}>
      <img src={url} alt={m.review.overview} style={{ width: "100%", height: "auto", display: "block" }} />
      {bbox && <div style={{
        position: "absolute", left: `${bbox[0] * 100}%`, top: `${bbox[1] * 100}%`, width: `${(bbox[2] - bbox[0]) * 100}%`,
        height: `${(bbox[3] - bbox[1]) * 100}%`, border: "2px solid var(--accent)", borderRadius: 3,
      }} />}
    </div>
  );
}

// R1: Vergleich zweier Bilder (Exemplar/Person) = Ähnlichkeit, Erkennung eines Labels = Sicherheit des Modells
const SIMILARITY_KINDS = new Set(["inventory_identity", "identity_match"]);

function Pictures({ r }: { r: ReviewRequest }) {
  const m = useMsgs();
  const box = { width: "min(200px, 100%)", height: 150, borderRadius: 8, overflow: "hidden", background: "var(--bg-4)", flex: "none" } as const;
  if (r.kind === "identity_match") {
    const d = r.data as { source_name?: string; target_name?: string };
    return (
      <div className="row" style={{ gap: 8, alignItems: "center", flex: "1 1 auto", minWidth: 0, flexWrap: "wrap" }}>
        <div style={{ textAlign: "center" }}><div style={{ ...box, width: 130, height: 130 }}><Img src={r.crop_url} className="contain-img" /></div>
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{d.source_name ?? m.review.unknown}</div></div>
        <ArrowRight size={18} color="var(--text-3)" />
        <div style={{ textAlign: "center" }}><div style={{ ...box, width: 130, height: 130 }}><Img src={r.snapshot_url} className="contain-img" /></div>
          <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{d.target_name ?? "?"}</div></div>
      </div>
    );
  }
  return (
    <div className="row" style={{ gap: 8, alignItems: "flex-start", flex: "0 1 auto", minWidth: 0, maxWidth: "100%", flexWrap: "wrap" }}>
      <div style={box}><Img src={r.crop_url} className="contain-img" /></div>
      {r.snapshot_url && <div style={{ width: "min(200px, 100%)" }}><Overview url={r.snapshot_url} bbox={r.bbox} /></div>}
    </div>
  );
}

function ReviewCard({ r, first, busy, labels, onAnswer }: {
  r: ReviewRequest; first: boolean; busy: boolean; labels: { label: string; name: string }[];
  onAnswer: (b: ReviewAnswerInput) => void;
}) {
  const m = useMsgs();
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
      else if (noMode) { if (k === "enter" || k === m.review.keyNo) onAnswer({ answer: "no", correct_label: correct || null }); }
      else if (k === m.review.keyYes) onAnswer({ answer: "yes" });
      else if (k === m.review.keyNo) no();
      else if (k === m.review.keySkip) onAnswer({ answer: "skip" });
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
        <div style={{ flex: 1, minWidth: "min(220px, 100%)" }}>
          <div className="row" style={{ gap: 6, marginBottom: 4 }}>
            <span className="badge outline">{m.review.kind[r.kind] ?? r.kind}</span>
            {reason && <span className="muted" style={{ fontSize: 12 }}>{m.review.reason[reason] ?? reason}</span>}
          </div>
          <div style={{ fontSize: 16, fontWeight: 600 }}>{r.question}</div>
          <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>
            {r.camera_name ? `${r.camera_name} · ` : ""}{fmtAgo(r.created_at)}
            {r.score !== null ? ` · ${SIMILARITY_KINDS.has(r.kind) ? m.review.similarity : m.review.confidence} ${Math.round(r.score * 100)} %` : ""}
            {r.threshold !== null ? m.review.required(Math.round(r.threshold * 100)) : ""}
          </div>
          {small && <div className="row" style={{ gap: 6, fontSize: 12, color: "#fbbf24", marginTop: 4 }}><AlertTriangle size={13} />{m.review.tooSmall}</div>}
          {noMode && (
            <div className="row" style={{ gap: 8, marginTop: 10, flexWrap: "wrap" }}>
              <label className="muted" style={{ fontSize: 13 }}>{m.review.insteadQuestion}</label>
              <select className="select input-sm" style={{ width: 220 }} value={correct} autoFocus onChange={(e) => setCorrect(e.target.value)}>
                <option value="">{m.review.dontKnow}</option>
                {labels.filter((l) => l.label !== r.label).map((l) => <option key={l.label} value={l.label}>{l.name}</option>)}
              </select>
              <button className="btn btn-sm" disabled={busy} onClick={() => onAnswer({ answer: "no", correct_label: correct || null })}><X size={14} />{m.review.sendNo}</button>
              <button className="btn btn-ghost btn-sm" onClick={() => setNoMode(false)}>{m.common.cancel}</button>
            </div>
          )}
        </div>
        {!noMode && (
          <div className="row" style={{ gap: 8 }}>
            <button className="btn btn-primary" disabled={busy} onClick={() => onAnswer({ answer: "yes" })} title={m.review.keyTitle(m.review.keyYes)}><Check size={16} />{m.common.yes}</button>
            <button className="btn" disabled={busy} onClick={no} title={m.review.keyTitle(m.review.keyNo)}><X size={16} />{m.common.no}</button>
            <button className="btn btn-ghost" disabled={busy} onClick={() => onAnswer({ answer: "skip" })} title={m.review.keyTitle(m.review.keySkip)}><SkipForward size={16} />{m.common.skip}</button>
          </div>
        )}
      </div>
    </div>
  );
}

function HistoryList() {
  const m = useMsgs();
  const q = useQuery({ queryKey: ["reviews", "history"], queryFn: () => api.reviews({ status: "yes,no,skipped,expired", limit: 100 }) });
  if (q.isLoading) return <Loading />;
  const items = q.data?.items ?? [];
  if (!items.length) return <Empty icon={<History size={44} />} title={m.review.noAnswers} />;
  return (
    <div className="card" style={{ overflow: "auto" }}>
      <table className="table">
        <thead><tr><th></th><th>{m.review.colQuestion}</th><th>{m.review.colAnswer}</th><th>{m.review.colCamera}</th><th>{m.review.colTime}</th><th>{m.review.colVia}</th></tr></thead>
        <tbody>
          {items.map((r) => (
            <tr key={r.id}>
              <td><Img src={r.crop_url} className="thumb" /></td>
              <td>{r.question}{r.correct_label && <div className="muted" style={{ fontSize: 12 }}>{m.review.instead(r.correct_label)}</div>}</td>
              <td><span className={`badge ${r.status === "yes" ? "ok" : r.status === "no" ? "bad" : "outline"}`}>{m.review.status[r.status]}</span></td>
              <td>{r.camera_name ?? "–"}</td>
              <td className="mono" style={{ whiteSpace: "nowrap" }}>{fmtDateTime(r.answered_at ?? r.expires_at ?? r.created_at)}</td>
              <td className="muted">{r.answer_source ? m.review.source[r.answer_source] ?? r.answer_source : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ReviewPage() {
  const m = useMsgs();
  const qc = useQueryClient();
  const [tab, setTab] = useState<"open" | "history">("open");
  const q = useQuery({ queryKey: ["reviews", "open"], queryFn: () => api.reviews({ status: "open" }) });
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const labels = (settings.data?.inventory_vocabulary ?? []).map((v) => ({ label: v.label, name: vocabDisplayName(v) }));
  const answer = useMutation({
    mutationFn: ({ id, b }: { id: number; b: ReviewAnswerInput }) => api.answerReview(id, b),
    onSuccess: (r) => {
      toast.success(m.review.answerSaved, `${r.question} – ${m.review.status[r.status]}`);
      qc.invalidateQueries({ queryKey: ["reviews"] });
      qc.invalidateQueries({ queryKey: ["inventory"] });
      qc.invalidateQueries({ queryKey: ["identities"] });
    },
    onError: (e: Error) => { toast.error(m.review.answerFailed, e.message); qc.invalidateQueries({ queryKey: ["reviews"] }); },
  });
  const d = q.data;
  return (
    <div className="page">
      <div className="page-header">
        <div><h1>{m.review.title}</h1>
          <div className="sub">{d ? m.review.summary(d.open_count, d.asked_today, d.max_per_day) : m.review.subtitle}</div>
        </div>
        <span className="muted row kbd-hint" style={{ gap: 6, fontSize: 12 }}><Keyboard size={14} />{m.review.keyHint}</span>
      </div>
      <div className="tabs">
        <button className={tab === "open" ? "on" : ""} onClick={() => setTab("open")}><CircleHelp size={15} />{m.review.tabOpen}<span className="count">{d?.open_count ?? 0}</span></button>
        <button className={tab === "history" ? "on" : ""} onClick={() => setTab("history")}><History size={15} />{m.review.tabHistory}</button>
      </div>
      {tab === "history" ? <HistoryList /> : q.isLoading ? <Loading /> : !d?.items.length ? (
        <Empty icon={<CircleHelp size={44} />} title={m.review.emptyTitle}
          text={m.review.emptyText} />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {d.items.map((r, i) => <ReviewCard key={r.id} r={r} first={i === 0} labels={labels} busy={answer.isPending}
            onAnswer={(b) => answer.mutate({ id: r.id, b })} />)}
        </div>
      )}
    </div>
  );
}
