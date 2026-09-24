import type { HighlightColor } from "@/lib/schema";

/** Swatches for the colour picker in the selection toolbar. */
export const HIGHLIGHT_COLOR_SWATCHES: {
	color: HighlightColor;
	bg: string;
	label: string;
}[] = [
	{ color: "yellow", bg: "bg-yellow-300", label: "Yellow" },
	{ color: "green", bg: "bg-green-400", label: "Green" },
	{ color: "blue", bg: "bg-blue-400", label: "Blue" },
	{ color: "pink", bg: "bg-pink-400", label: "Pink" },
	{ color: "purple", bg: "bg-purple-400", label: "Purple" },
];

/**
 * Highlight fills resolve to CSS custom properties, defined on
 * `.pdf-highlight-root` in globals.css with a `.dark` override.
 *
 * The values deliberately do not live here. A highlight rect tints the page
 * canvas through a blend mode, and the blend mode is theme-dependent:
 * `multiply` over a white page, `screen` over the inverted dark one. Each needs
 * its own palette — a pale fill under `screen` blows out to white and erases
 * the text it was meant to tint.
 *
 * Picking the palette in JS (from a React "is dark" hook) while the blend mode
 * came from the `.dark` class meant the two could disagree — during hydration,
 * or any time the class is set outside React — and produce exactly that
 * text-erasing failure. Naming a variable instead makes the theme the single
 * source of truth for both halves.
 */
export function getUserHighlightFill(
	color: HighlightColor | null | undefined,
	isActive: boolean
): string {
	return `var(--hl-${color || "blue"}${isActive ? "-active" : ""})`;
}

export function getAssistantHighlightFill(isActive: boolean): string {
	return `var(--hl-assistant${isActive ? "-active" : ""})`;
}
