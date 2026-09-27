/** Viewport-space box of what's actually visible of a scroll container. */
export interface VisibleBounds {
	left: number;
	top: number;
	right: number;
	bottom: number;
}

/**
 * The scroller's padding box (no scrollbars) intersected with the visual
 * viewport, so a popover clamped to it is on screen even when the pages are
 * zoomed past the pane width or the browser itself is pinch-zoomed.
 */
export function visibleBounds(scroller: HTMLElement): VisibleBounds {
	const r = scroller.getBoundingClientRect();
	const left = r.left + scroller.clientLeft;
	const top = r.top + scroller.clientTop;
	let bounds = {
		left,
		top,
		right: left + scroller.clientWidth,
		bottom: top + scroller.clientHeight,
	};
	const vv = window.visualViewport;
	if (vv) {
		// Layout-viewport coordinates of the visual viewport.
		const vLeft = vv.offsetLeft;
		const vTop = vv.offsetTop;
		const next = {
			left: Math.max(bounds.left, vLeft),
			top: Math.max(bounds.top, vTop),
			right: Math.min(bounds.right, vLeft + vv.width),
			bottom: Math.min(bounds.bottom, vTop + vv.height),
		};
		if (next.right > next.left && next.bottom > next.top) bounds = next;
	}
	return bounds;
}

/**
 * Viewport → the scroller's content coordinates, for an `absolute` child
 * portalled into it (so it scrolls with the pages).
 */
export function toScrollerContent(scroller: HTMLElement, x: number, y: number) {
	const r = scroller.getBoundingClientRect();
	return {
		left: x - r.left - scroller.clientLeft + scroller.scrollLeft,
		top: y - r.top - scroller.clientTop + scroller.scrollTop,
	};
}
