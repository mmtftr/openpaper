"use client";

import { useEffect, useLayoutEffect, useState } from "react";
import type { CSSProperties, RefObject } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { useAtomValue, useSetAtom } from "jotai";
import { toast } from "sonner";
import {
	ArrowUpRight,
	BookOpen,
	Check,
	ExternalLink,
	FileText,
	Library,
	Loader2,
	Plus,
} from "lucide-react";
import { uploadFromUrlWithFallback } from "@/lib/uploadUtils";
import { citationPreviewAtom } from "../atoms";

const CARD_WIDTH = 540;
const GAP = 12;
const VIEWPORT_MARGIN = 8;
const ESTIMATED_HEIGHT = 220;

/**
 * Prefer a 540px card below the anchor with a 12px gap; clamp horizontally
 * inside the scroller and flip above when there is no room below.
 */
function computePosition(
	anchorRect: DOMRect,
	scroller: HTMLElement
): { left: number; top: number; width: number } {
	const scrollRect = scroller.getBoundingClientRect();
	const width = Math.min(CARD_WIDTH, scrollRect.width - VIEWPORT_MARGIN * 2);
	let left = anchorRect.left + anchorRect.width / 2 - width / 2;
	left = Math.max(
		scrollRect.left + VIEWPORT_MARGIN,
		Math.min(left, scrollRect.right - width - VIEWPORT_MARGIN)
	);

	const spaceBelow = scrollRect.bottom - anchorRect.bottom;
	const flip =
		spaceBelow < ESTIMATED_HEIGHT + GAP &&
		anchorRect.top - scrollRect.top > ESTIMATED_HEIGHT + GAP;
	// Coordinates are relative to the scroller's content box (the portal target).
	const top = flip
		? anchorRect.top - scrollRect.top - GAP - ESTIMATED_HEIGHT + scroller.scrollTop
		: anchorRect.bottom - scrollRect.top + GAP + scroller.scrollTop;
	return { left: left - scrollRect.left, top, width };
}

function formatYear(date: string | null): string | null {
	if (!date) return null;
	const match = date.match(/(19|20)\d{2}/);
	return match ? match[0] : null;
}

/** Rendered first page for library papers; a typographic stand-in otherwise. */
function Thumbnail({
	previewUrl,
	inLibrary,
}: {
	previewUrl?: string | null;
	inLibrary: boolean;
}) {
	if (previewUrl) {
		return (
			<div className="h-[125px] w-[100px] shrink-0 overflow-hidden rounded-lg border border-border bg-white dark:bg-neutral-900">
				{/* eslint-disable-next-line @next/next/no-img-element */}
				<img
					src={previewUrl}
					alt=""
					className="thumbnail-canvas h-full w-full object-cover object-top"
					loading="lazy"
				/>
			</div>
		);
	}
	return (
		<div className="flex h-[125px] w-[100px] shrink-0 items-center justify-center rounded-lg border border-border bg-muted">
			{inLibrary ? (
				<Library className="size-8 text-blue-500" />
			) : (
				<FileText className="size-8 text-muted-foreground" />
			)}
		</div>
	);
}

/**
 * Hover/click preview for a citation.
 *
 * Four states, matching what resolution can actually tell apart: still loading,
 * matched to a paper, found the bibliography text but matched nothing, and
 * couldn't reach the lookup at all. The last two are deliberately distinct —
 * "no match" and "offline" mean different things to someone deciding whether
 * the reference is worth chasing.
 */
export default function CitationPreviewCard({
	scrollerRef,
	onJumpToPage,
}: {
	scrollerRef: RefObject<HTMLElement | null>;
	onJumpToPage?: (page: number) => void;
}) {
	const preview = useAtomValue(citationPreviewAtom);
	const setPreview = useSetAtom(citationPreviewAtom);
	const [pos, setPos] = useState<{
		left: number;
		top: number;
		width: number;
	} | null>(null);
	const [importing, setImporting] = useState(false);
	const [imported, setImported] = useState(false);

	useLayoutEffect(() => {
		if (!preview || !scrollerRef.current) {
			setPos(null);
			return;
		}
		setPos(computePosition(preview.anchorRect, scrollerRef.current));
	}, [preview, scrollerRef]);

	// A new citation is a new subject; drop any import result from the last one.
	useEffect(() => {
		setImported(false);
		setImporting(false);
	}, [preview]);

	const [visible, setVisible] = useState(false);
	useEffect(() => {
		if (!preview) {
			setVisible(false);
			return;
		}
		const id = requestAnimationFrame(() => setVisible(true));
		return () => cancelAnimationFrame(id);
	}, [preview]);

	if (!preview || !pos) return null;

	const style: CSSProperties = {
		position: "absolute",
		left: pos.left,
		top: pos.top,
		width: pos.width,
		opacity: visible ? 1 : 0,
		transform: visible ? "translateY(0)" : "translateY(6px)",
		transition:
			"opacity 180ms ease-out, transform 180ms cubic-bezier(0.2, 0.9, 0.3, 1)",
	};

	const destinationPage =
		preview.state === "raw" || preview.state === "paper"
			? preview.destinationPage
			: null;

	const jumpButton = destinationPage ? (
		<button
			className="rounded-lg border border-border px-3 py-1 text-xs font-medium hover:bg-muted"
			onClick={() => {
				onJumpToPage?.(destinationPage);
				setPreview(null);
			}}
		>
			Jump to page {destinationPage}
		</button>
	) : null;

	const paper = preview.state === "paper" ? preview.paper : null;
	const importUrl = paper && !paper.paperId ? paper.pdfUrl : null;

	const addToLibrary = async () => {
		if (!importUrl || importing) return;
		setImporting(true);
		try {
			await uploadFromUrlWithFallback(importUrl);
			setImported(true);
			toast.success("Added to your library — processing in the background.");
		} catch (error) {
			console.error("Failed to import cited paper:", error);
			toast.error(
				error instanceof Error ? error.message : "Could not add that paper."
			);
		} finally {
			setImporting(false);
		}
	};

	let body: React.ReactNode = null;
	switch (preview.state) {
		case "skeleton":
			body = (
				<div className="flex animate-pulse gap-4 p-4">
					<div className="h-[125px] w-[100px] shrink-0 rounded-lg bg-border" />
					<div className="flex flex-1 flex-col gap-2 pt-1">
						<div className="h-4 w-4/5 rounded bg-border" />
						<div className="h-3 w-full rounded bg-border" />
						<div className="h-3 w-11/12 rounded bg-border" />
						<div className="h-3 w-2/3 rounded bg-border" />
					</div>
				</div>
			);
			break;
		case "paper": {
			const year = formatYear(paper!.publicationDate);
			const meta = [paper!.venue, year].filter(Boolean).join(" · ");
			body = (
				<div className="flex gap-4 p-4">
					<Thumbnail
						previewUrl={paper!.previewUrl}
						inLibrary={Boolean(paper!.paperId)}
					/>
					<div className="min-w-0 flex-1">
						{paper!.paperId && (
							<p className="mb-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-blue-500">
								<Library className="size-3" /> In your library
							</p>
						)}
						<p className="line-clamp-2 text-sm font-semibold leading-snug">
							{paper!.title}
						</p>
						{paper!.authors.length > 0 && (
							<p className="mt-0.5 truncate text-xs text-muted-foreground">
								{paper!.authors.slice(0, 6).join(", ")}
								{paper!.authors.length > 6 ? " et al." : ""}
							</p>
						)}
						{meta && (
							<p className="mt-0.5 truncate text-[11px] text-muted-foreground">
								{meta}
							</p>
						)}
						{paper!.abstract && (
							<p className="mt-1 line-clamp-3 text-xs leading-relaxed text-muted-foreground">
								{paper!.abstract}
							</p>
						)}
						{paper!.topics.length > 0 && (
							<div className="mt-1.5 flex flex-wrap gap-1">
								{paper!.topics.slice(0, 4).map((t) => (
									<span
										key={t}
										className="rounded-full bg-blue-500/10 px-2 py-0.5 text-[10px] text-blue-500"
									>
										#{t}
									</span>
								))}
							</div>
						)}
					</div>
				</div>
			);
			break;
		}
		case "raw":
			body = (
				<div className="p-4">
					<p className="mb-1.5 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
						<BookOpen className="size-3" /> Reference
					</p>
					<p className="line-clamp-4 text-xs leading-relaxed text-foreground">
						{preview.referenceText}
					</p>
				</div>
			);
			break;
		case "unavailable":
			body = (
				<div className="flex items-center gap-3 p-4">
					<ExternalLink className="size-4 shrink-0 text-muted-foreground" />
					<p className="text-xs text-muted-foreground">
						Preview unavailable — could not reach the lookup service.
					</p>
				</div>
			);
			break;
	}

	const openTarget = paper
		? paper.paperId
			? `/paper/${paper.paperId}`
			: paper.externalUrl
		: null;

	const hasFooter = Boolean(jumpButton || openTarget || importUrl);

	return createPortal(
		<div
			data-citation-preview
			style={style}
			className="z-20 overflow-hidden rounded-xl border border-border bg-popover shadow-lg"
		>
			{body}
			{hasFooter && (
				<div className="flex items-center justify-between gap-2 border-t border-border px-4 py-2">
					<div>{jumpButton}</div>
					<div className="flex items-center gap-2">
						{importUrl &&
							(imported ? (
								<span className="flex items-center gap-1 rounded-lg border border-border px-3 py-1 text-xs font-medium text-muted-foreground">
									<Check className="size-3" /> Added
								</span>
							) : (
								<button
									onClick={addToLibrary}
									disabled={importing}
									className="flex items-center gap-1 rounded-lg border border-border px-3 py-1 text-xs font-medium hover:bg-muted disabled:opacity-60"
								>
									{importing ? (
										<Loader2 className="size-3 animate-spin" />
									) : (
										<Plus className="size-3" />
									)}
									{importing ? "Adding…" : "Add to library"}
								</button>
							))}
						{openTarget &&
							(paper?.paperId ? (
								<Link
									href={openTarget}
									className="flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:opacity-90"
								>
									Open <ArrowUpRight className="size-3" />
								</Link>
							) : (
								<a
									href={openTarget}
									target="_blank"
									rel="noopener noreferrer"
									className="flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:opacity-90"
								>
									Open <ArrowUpRight className="size-3" />
								</a>
							))}
					</div>
				</div>
			)}
		</div>,
		scrollerRef.current ?? document.body
	);
}
