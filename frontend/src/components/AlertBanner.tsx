import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCheck, Siren } from "lucide-react";
import { api } from "../lib/api";
import { eventAlert, fmtDuration, POSTURE_NAME } from "../lib/pose";
import { toast } from "../lib/store";
import type { VGEvent } from "../lib/types";
import { EventDetailDialog } from "./EventBits";
import { msgs, useMsgs } from "../i18n";
import { labelName } from "../lib/labels";

/** Sekunden seit `iso` (aktualisiert sich jede Sekunde) */
function useSince(iso?: string | null): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  return iso ? Math.max(0, (now - Date.parse(iso)) / 1000) : 0;
}

export function alertText(e: VGEvent): string {
  const a = eventAlert(e);
  const p = a?.posture ? POSTURE_NAME[a.posture] : null;
  const who = e.identity?.display_name ?? labelName(e.label);
  return p ? `${who} ${p}` : `${who}: ${a?.rule ?? msgs().ui.alert.fallback}`;
}

function AlertRow({ e, onOpen }: { e: VGEvent; onOpen: () => void }) {
  const qc = useQueryClient();
  const m = useMsgs().ui.alert;
  const a = eventAlert(e);
  const since = useSince(a?.posture_since ?? a?.first_fired_at ?? e.start_ts);
  const ack = useMutation({
    mutationFn: () => api.ackEvent(e.id),
    onSuccess: () => { toast.success(m.acknowledged, alertText(e)); qc.invalidateQueries({ queryKey: ["events"] }); },
    onError: (err: Error) => toast.error(m.ackFailed, err.message),
  });
  return (
    <div className="alert-row">
      <Siren size={18} className="alert-ico" />
      <button type="button" className="alert-text" onClick={onOpen} title={m.openEvent}>
        <b>{alertText(e)}</b> {m.since(fmtDuration(since))} · {m.camera(e.camera_name)}
        <span className="muted"> · {m.rule(a?.rule ?? "")}{a?.repeat ? ` · ${m.repeated(a.repeat)}` : ""}</span>
      </button>
      <button className="btn btn-sm btn-danger" disabled={ack.isPending} onClick={() => ack.mutate()}>
        {ack.isPending ? <span className="spinner" /> : <CheckCheck size={15} />}{m.acknowledge}
      </button>
    </div>
  );
}

/** W2 (C1): offene Notfälle (z. B. „Person liegt am Boden“) als Banner über jeder Seite, mit „Quittieren“.
 *  Daten: GET /events?alert=true&active=true&acknowledged=false; WS-Events invalidieren ["events"]. */
export function AlertBanner() {
  const q = useQuery({
    queryKey: ["events", "alerts-open"],
    queryFn: () => api.events({ alert: true, active: true, acknowledged: false, limit: 5 }),
    refetchInterval: 30_000,
  });
  const [open, setOpen] = useState<VGEvent | null>(null);
  const m = useMsgs().ui.alert;
  const items = q.data?.items ?? [];
  if (!items.length && !open) return null;
  return (
    <>
      {items.length > 0 && (
        <div className="alert-banner" role="alert">
          {items.map((e) => <AlertRow key={e.id} e={e} onOpen={() => setOpen(e)} />)}
          {(q.data?.total ?? 0) > items.length && <div className="muted" style={{ fontSize: 12 }}>{m.andMore((q.data?.total ?? 0) - items.length)}</div>}
        </div>
      )}
      {open && <EventDetailDialog event={open} onClose={() => setOpen(null)} />}
    </>
  );
}
