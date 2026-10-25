import { AlertTriangle, CheckCircle2, HelpCircle } from "lucide-react";
import type { CameraHealthStatus, HealthProblemType } from "../lib/types";

export const HEALTH_LABEL: Record<HealthProblemType, string> = {
  dark: "Zu dunkel", blur: "Unscharf", covered: "Verdeckt", frozen: "Eingefroren", moved: "Verschoben",
};

/** Ampel der Kamera-Gesundheit (C5). `full` zeigt auch „OK“/„unbekannt“, sonst nur Warnungen. */
export function HealthBadge({ health, full = false }: { health: CameraHealthStatus | null | undefined; full?: boolean }) {
  if (!health) return full ? <span className="badge" title="Noch keine Bildprüfung"><HelpCircle size={11} />–</span> : null;
  if (health.state === "warn" && health.problems.length) {
    const first = health.problems[0];
    return (
      <span className="badge warn" title={health.problems.map((p) => p.text).join("\n")}>
        <AlertTriangle size={11} />{HEALTH_LABEL[first.type] ?? first.type}{health.problems.length > 1 ? ` +${health.problems.length - 1}` : ""}
      </span>
    );
  }
  if (!full) return null;
  if (health.state === "unknown") return <span className="badge" title="Keine aktuelle Bildprüfung (Kamera offline?)"><HelpCircle size={11} />unbekannt</span>;
  return <span className="badge ok" style={{ animation: "none" }} title="Bild in Ordnung"><CheckCircle2 size={11} />Bild OK</span>;
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
