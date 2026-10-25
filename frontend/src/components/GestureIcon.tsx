import type { GestureName } from "../lib/types";

// Strichmännchen-Piktogramme der Armgesten (Blick auf die Person: ihr linker Arm ist im Bild rechts)
const ARMS: Record<GestureName, string> = {
  arms_up: "M9 10 L7 3 M15 10 L17 3",
  arm_left: "M15 10 L22 10 M9 10 L7 16",
  arm_right: "M9 10 L2 10 M15 10 L17 16",
  arms_cross: "M9 10 L7 5 L14 1 M15 10 L17 5 L10 1",
};

export function GestureIcon({ g, size = 22 }: { g: GestureName; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="6" r="2.2" />
      <path d="M12 8.5 V15 M12 15 L9 22 M12 15 L15 22 M9 10 H15" />
      <path d={ARMS[g]} />
    </svg>
  );
}
