"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { PaperHighlight } from "@/lib/schema";
import { READER_POPOVER_SELECTOR, type HighlightHit } from "./HighlightLayer";

/** Same feel as the citation preview (useCitationLinks). */
const OPEN_DELAY_MS = 120;
const CLOSE_DELAY_MS = 150;
/** A clicked popover is given a little longer to be reached. */
const ENGAGED_CLOSE_DELAY_MS = 300;

/** Mouse/trackpad: hover means something. Touch has no hover to leave. */
function canHover(): boolean {
	return window.matchMedia("(hover: hover) and (pointer: fine)").matches;
}

/** The user is typing in the popover (reply, edit or new note field). */
function typingInPopover(): boolean {
	const el = document.activeElement as HTMLElement | null;
	if (!el?.closest(READER_POPOVER_SELECTOR)) return false;
	return el.tagName === "TEXTAREA" || el.tagName === "INPUT" || el.isContentEditable;
}

export interface HighlightPopoverTarget {
	highlightId: string;
	/** Line rect to anchor to; -1 means the last one (where a selection ended). */
	rectIndex: number;
	/**
	 * Engaged popovers were clicked, tapped or typed into: hover no longer moves
	 * them. With a mouse they still close once neither hovered nor typed in
	 * (never with an unsaved draft); on touch, Escape or a tap elsewhere.
	 */
	engaged: boolean;
	/** Show the new-note composer even though the highlight has no notes yet. */
	compose: boolean;
	/** Created by "Comment" on a selection: drop it if abandoned without a note. */
	createdForNote: boolean;
}

/**
 * Hover-intent state for the popover shown over a highlight — the note thread
 * when it has notes, the highlight toolbar when it doesn't.
 *
 * Deliberately independent of the page's `activeHighlight`: that drives the
 * side panel (tab switches, scrolling, resetting drafts), and a hover must not.
 *
 * States: closed → pending (open timer) → open (transient) → engaged. Every
 * dismissal cancels both timers, and the open timer re-checks eligibility when
 * it fires, so a stale timer can't resurrect a popover.
 */
export function useHighlightPopover() {
	const [target, setTarget] = useState<HighlightPopoverTarget | null>(null);
	const targetRef = useRef(target);
	targetRef.current = target;

	const openTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
	const dirtyRef = useRef(false);
	const pointerOverPopover = useRef(false);
	/** What the pointer is over right now, per the latest hover report. */
	const hoveredIdRef = useRef<string | null>(null);

	const cancelOpen = useCallback(() => {
		if (openTimer.current) clearTimeout(openTimer.current);
		openTimer.current = null;
	}, []);
	const cancelClose = useCallback(() => {
		if (closeTimer.current) clearTimeout(closeTimer.current);
		closeTimer.current = null;
	}, []);

	const close = useCallback(() => {
		cancelOpen();
		cancelClose();
		dirtyRef.current = false;
		pointerOverPopover.current = false;
		setTarget(null);
	}, [cancelOpen, cancelClose]);

	const scheduleClose = useCallback(() => {
		if (closeTimer.current) return;
		const engaged = targetRef.current?.engaged ?? false;
		if (engaged && !canHover()) return;
		closeTimer.current = setTimeout(() => {
			closeTimer.current = null;
			const cur = targetRef.current;
			if (!cur || pointerOverPopover.current) return;
			if (
				cur.engaged &&
				(dirtyRef.current ||
					typingInPopover() ||
					hoveredIdRef.current === cur.highlightId)
			) {
				return;
			}
			close();
		}, engaged ? ENGAGED_CLOSE_DELAY_MS : CLOSE_DELAY_MS);
	}, [close]);

	/** From HighlightLayer: the hovered highlight changed (null = none). */
	const hover = useCallback(
		(hit: HighlightHit | null) => {
			const cur = targetRef.current;
			hoveredIdRef.current = hit?.highlight.id ?? null;
			cancelOpen();
			if (cur?.engaged) {
				if (hit?.highlight.id === cur.highlightId) cancelClose();
				else scheduleClose();
				return;
			}
			if (hit && cur && hit.highlight.id === cur.highlightId) {
				cancelClose();
				return;
			}
			if (hit?.highlight.id) {
				const { highlight, rectIndex } = hit;
				openTimer.current = setTimeout(() => {
					openTimer.current = null;
					// Re-check at fire time: the pointer may have moved on (or into
					// the open popover), a drag-select may have started, or the user
					// engaged something else since this was scheduled.
					if (hoveredIdRef.current !== highlight.id) return;
					if (pointerOverPopover.current) return;
					if (targetRef.current?.engaged) return;
					const sel = window.getSelection();
					if (sel && !sel.isCollapsed && sel.toString().trim()) return;
					cancelClose();
					setTarget({
						highlightId: highlight.id!,
						rectIndex,
						engaged: false,
						compose: false,
						createdForNote: false,
					});
				}, OPEN_DELAY_MS);
			}
			if (cur) scheduleClose();
		},
		[cancelOpen, cancelClose, scheduleClose]
	);

	/** Click / tap on a highlight, or an explicit action like "Comment". */
	const engage = useCallback(
		(
			highlight: PaperHighlight,
			rectIndex: number,
			opts: { compose?: boolean; createdForNote?: boolean } = {}
		) => {
			if (!highlight.id) return;
			cancelOpen();
			cancelClose();
			dirtyRef.current = false;
			setTarget({
				highlightId: highlight.id,
				rectIndex,
				engaged: true,
				compose: opts.compose ?? false,
				createdForNote: opts.createdForNote ?? false,
			});
		},
		[cancelOpen, cancelClose]
	);

	/** The user pressed, focused or typed inside the open popover. */
	const engageCurrent = useCallback(() => {
		cancelClose();
		setTarget((prev) => (prev && !prev.engaged ? { ...prev, engaged: true } : prev));
	}, [cancelClose]);

	const setCompose = useCallback((highlightId: string) => {
		setTarget((prev) =>
			prev && prev.highlightId === highlightId
				? { ...prev, engaged: true, compose: true }
				: prev
		);
	}, []);

	const popoverEnter = useCallback(() => {
		pointerOverPopover.current = true;
		// HighlightLayer forgets its last hit over a popover (and won't report
		// the "none" that follows), so neither may this.
		hoveredIdRef.current = null;
		cancelOpen();
		cancelClose();
	}, [cancelOpen, cancelClose]);
	const popoverLeave = useCallback(() => {
		pointerOverPopover.current = false;
		scheduleClose();
	}, [scheduleClose]);

	/** A click away from the popover. Keeps it while a draft is unsaved. */
	const clickAway = useCallback(() => {
		cancelOpen();
		if (!targetRef.current) return;
		if (dirtyRef.current) return;
		close();
	}, [cancelOpen, close]);

	/**
	 * A press on the pages: a selection may be starting, so nothing new opens.
	 * An open transient popover is left to the drag itself (hover reports
	 * nothing while a button is down) — closing here would make a click on the
	 * hovered highlight flash the popover closed and open again.
	 */
	const pagePointerDown = useCallback(() => {
		cancelOpen();
	}, [cancelOpen]);

	const isDirty = useCallback(() => dirtyRef.current, []);

	const setDirty = useCallback((dirty: boolean) => {
		dirtyRef.current = dirty;
		if (dirty) engageCurrent();
	}, [engageCurrent]);

	useEffect(
		() => () => {
			cancelOpen();
			cancelClose();
		},
		[cancelOpen, cancelClose]
	);

	return {
		target,
		hover,
		engage,
		engageCurrent,
		setCompose,
		close,
		clickAway,
		pagePointerDown,
		popoverEnter,
		popoverLeave,
		setDirty,
		isDirty,
		cancelPending: cancelOpen,
	};
}

export type HighlightPopoverController = ReturnType<typeof useHighlightPopover>;
