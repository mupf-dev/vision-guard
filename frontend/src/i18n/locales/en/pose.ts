/** Posture and gesture names (lib/pose.ts). */
const pose = {
  posture: { lying: "lying", sitting: "sitting", standing: "standing", unknown: "unknown" } as Record<string, string>,
  postureTrigger: { lying: "lying on the floor", sitting: "sitting", standing: "standing" } as Record<string, string>,
  gesture: {
    arms_up: "Both arms up",
    arm_left: "Left arm out",
    arm_right: "Right arm out",
    arms_cross: "Arms crossed above head",
  } as Record<string, string>,
  gestureHint: {
    arms_up: "both hands above the head",
    arm_left: "the person's left arm held out sideways (from the person's view)",
    arm_right: "the person's right arm held out sideways (from the person's view)",
    arms_cross: "forearms crossed in an X above head/shoulder height",
  } as Record<string, string>,
  durSec: (s: number) => `${s} s`,
  durMinSec: (m: number, ss: string) => `${m}:${ss} min`,
  durHourMin: (h: number, m: number) => `${h} h ${m} min`,
};

export type PoseMessages = typeof pose;
export default pose;
