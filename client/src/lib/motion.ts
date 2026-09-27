/**
 * Motion presets for `motion/react`, matching the CSS tokens in globals.css
 * (`--ease-out-soft`, `--ease-in-out-soft`). Durations are in seconds.
 */
export const EASE_OUT_SOFT = [0.22, 1, 0.36, 1] as const;
export const EASE_IN_OUT_SOFT = [0.65, 0, 0.35, 1] as const;

/** The sliding "active" pill behind tabs and nav items. */
export const PILL_SPRING = { type: "spring", stiffness: 520, damping: 40, mass: 0.8 } as const;
