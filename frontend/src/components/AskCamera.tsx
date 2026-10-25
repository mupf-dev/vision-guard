import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { MessageCircleQuestion } from "lucide-react";
import { api } from "../lib/api";
import type { AskResult } from "../lib/types";
import { Dialog } from "./ui";

const TEMPLATES = ["Is the light on?", "Is the window open?", "Is there a person?", "Is the stove on?"];

/**
 * „Frag die Kamera“ (W3, Gerüst K3 – Ausbau A2: Rahmen ziehen, Standbild, Verlauf). Fragen am besten englisch.
 */
export function AskCameraButton({ cameraId }: { cameraId: number }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className="btn btn-sm" onClick={() => setOpen(true)} title="Frag die Kamera"><MessageCircleQuestion size={15} />Frag die Kamera</button>
      {open && <AskCameraDialog cameraId={cameraId} onClose={() => setOpen(false)} />}
    </>
  );
}

export function AskCameraDialog({ cameraId, onClose }: { cameraId: number; onClose: () => void }) {
  const [question, setQuestion] = useState("");
  const [res, setRes] = useState<AskResult | null>(null);
  const ask = useMutation({ mutationFn: () => api.askCamera(cameraId, { question: question.trim() }), onSuccess: setRes });
  return (
    <Dialog title="Frag die Kamera" onClose={onClose}>
      <form className="row-wrap" style={{ gap: 8 }} onSubmit={(e) => { e.preventDefault(); if (question.trim()) ask.mutate(); }}>
        <input className="input" style={{ flex: 1, minWidth: 220 }} maxLength={300} value={question} placeholder="Frage (englisch)"
          onChange={(e) => setQuestion(e.target.value)} />
        <button className="btn btn-primary" type="submit" disabled={!question.trim() || ask.isPending}>{ask.isPending ? "…" : "Fragen"}</button>
      </form>
      <div className="row-wrap" style={{ gap: 6, marginTop: 8 }}>
        {TEMPLATES.map((t) => <button key={t} className="btn btn-ghost btn-sm" onClick={() => setQuestion(t)}>{t}</button>)}
      </div>
      {ask.isError && <div className="hint" style={{ color: "var(--danger)", marginTop: 12 }}>{(ask.error as Error).message}</div>}
      {res && <div style={{ marginTop: 12 }}><b>{res.answer}</b> <span className="muted">({res.engine === "cloud" ? "Cloud" : "lokal"}, {Math.round(res.ms)} ms)</span></div>}
    </Dialog>
  );
}
