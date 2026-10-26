import { AlertTriangle, CheckCircle2, HelpCircle } from "lucide-react";
import type { CameraHealthStatus, HealthProblemType } from "../lib/types";
import { msgs, useMsgs } from "../i18n";

/** Namen der Bildprobleme (lokalisiert, Zugriff wie auf ein Objekt). */
export const HEALTH_LABEL = new Proxy({} as Record<HealthProblemType, string>, {
  get: (_t, k) => (typeof k === "string" ? msgs().ui.health.labels[k] : undefined),
});

/** Ampel der Kamera-Gesundheit (C5). `full` zeigt auch „OK“/„unbekannt“, sonst nur Warnungen. */
export function HealthBadge({ health, full = false }: { health: CameraHealthStatus | null | undefined; full?: boolean }) {
  const m = useMsgs().ui.health;
  if (!health) return full ? <span className="badge" title={m.noCheckYet}><HelpCircle size={11} />–</span> : null;
  if (health.state === "warn" && health.problems.length) {
    const first = health.problems[0];
    return (
      <span className="badge warn" title={health.problems.map((p) => p.text).join("\n")}>
        <AlertTriangle size={11} />{HEALTH_LABEL[first.type] ?? first.type}{health.problems.length > 1 ? ` +${health.problems.length - 1}` : ""}
      </span>
    );
  }
  if (!full) return null;
  if (health.state === "unknown") return <span className="badge" title={m.noCurrentCheck}><HelpCircle size={11} />{m.unknown}</span>;
  return <span className="badge ok" style={{ animation: "none" }} title={m.okTitle}><CheckCircle2 size={11} />{m.ok}</span>;
}

/** Gelbes Banner im Live-Bild („Verdeckt seit 14:02“). */
export function HealthBanner({ health }: { health: CameraHealthStatus | null | undefined }) {
  if (!health || health.state !== "warn" || !health.problems.length) return null;
  return (
    <div className="health-banner" title={health.problems.map((p) => p.text).join("\n")}>
      <AlertTriangle size={14} />
      <span>{health.problems.map((p) => p.text).join(" · ")}</span>
    </div>
  );
}
