import { AlertTriangle, BellRing, CheckCircle2, Info, X } from "lucide-react";
import { useToasts } from "../lib/store";

export function Toasts() {
  const { toasts, dismiss } = useToasts();
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>
          {t.image ? <img src={t.image} alt="" /> : (
            <span className="ti">
              {t.kind === "success" ? <CheckCircle2 size={18} /> : t.kind === "error" ? <AlertTriangle size={18} /> : t.kind === "event" ? <BellRing size={18} /> : <Info size={18} />}
            </span>
          )}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="tt">{t.title}</div>
            {t.text && <div className="tx">{t.text}</div>}
          </div>
          <button className="btn btn-ghost btn-icon btn-sm" onClick={() => dismiss(t.id)} aria-label="Schließen"><X size={14} /></button>
        </div>
      ))}
    </div>
  );
}
