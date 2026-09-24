// Shared motion presets so panel transitions feel like one system.
export const easeOut = [0.22, 1, 0.36, 1] as const;
export const pillSpring = {
  type: "spring",
  stiffness: 560,
  damping: 40,
  mass: 0.7,
} as const;
// Small offset only: the station name itself flies in via a shared layoutId.
export const enterFromRight = {
  initial: { opacity: 0, x: 8 },
  animate: { opacity: 1, x: 0 },
  transition: { duration: 0.28, ease: easeOut },
} as const;
