"use client";

import { MotionConfig } from "motion/react";

/** `reducedMotion="user"`: transform/layout animations respect the OS setting. */
export function MotionProvider({ children }: { children: React.ReactNode }) {
	return <MotionConfig reducedMotion="user">{children}</MotionConfig>;
}
