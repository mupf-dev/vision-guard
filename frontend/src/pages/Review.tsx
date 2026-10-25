import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleHelp, SkipForward, X } from "lucide-react";
import { api } from "../lib/api";
import type { ReviewRequest } from "../lib/types";
import { fmtAgo } from "../lib/format";
import { Empty, Img, Loading } from "../components/ui";
import { toast } from "../lib/store";

/**
 * Seite „Bitte bestätigen“ (W2, Gerüst K2 – Ausbau D2): offene Rückfragen mit Bildausschnitt, Ja/Nein/Überspringen.
 * D2 ergänzt: Tasten J/N/S, „Was ist es stattdessen?“ (correct_label), Übersichtsbild, Filter, Verlauf.
 */
function ReviewCard({ r, onAnswer, busy }: { r: ReviewRequest; onAnswer: (a: "yes" | "no" | "skip") => void; busy: boolean }) {
  return (
    <div className="card">
      <div className="card-body" style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ width: 160, height: 120, borderRadius: 8, overflow: "hidden", background: "var(--bg-4)", flex: "none" }}>
          <Img src={r.crop_url} className="contain-img" />
        </div>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ fontSize: 16, fontWeight: 600 }}>{r.question}</div>
          <div className="muted" style={{ fontSize: 13 }}>
            {r.camera_name ?? "–"} · {fmtAgo(r.created_at)}{r.score !== null ? ` · Score ${r.score.toFixed(2)}` : ""}
          </div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <button className="btn btn-primary" disabled={busy} onClick={() => onAnswer("yes")}><Check size={16} />Ja</button>
          <button className="btn" disabled={busy} onClick={() => onAnswer("no")}><X size={16} />Nein</button>
          <button className="btn btn-ghost" disabled={busy} onClick={() => onAnswer("skip")}><SkipForward size={16} />Überspringen</button>
        </div>
      </div>
    </div>
  );
}

export function ReviewPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["reviews", "open"], queryFn: () => api.reviews({ status: "open" }) });
  const answer = useMutation({
    mutationFn: ({ id, a }: { id: number; a: "yes" | "no" | "skip" }) => api.answerReview(id, { answer: a }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["reviews"] }),
    onError: (e: Error) => toast.error("Antwort nicht gespeichert", e.message),
  });
  if (q.isLoading) return <Loading />;
  const d = q.data;
  return (
    <div className="page">
      <div className="page-header">
        <div><h1>Bitte bestätigen</h1>
          <div className="sub">{d ? `${d.open_count} offen · heute ${d.asked_today}/${d.max_per_day} gefragt` : "Rückfragen der Erkennung"}</div>
        </div>
      </div>
      {!d?.items.length ? (
        <Empty icon={<CircleHelp size={44} />} title="Keine offenen Fragen"
          text="Bei unsicheren Erkennungen fragt VisionGuard hier nach – Ihre Antworten verbessern die Erkennung." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {d.items.map((r) => <ReviewCard key={r.id} r={r} busy={answer.isPending} onAnswer={(a) => answer.mutate({ id: r.id, a })} />)}
        </div>
      )}
    </div>
  );
}
