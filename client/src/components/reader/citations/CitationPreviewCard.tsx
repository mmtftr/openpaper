"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode, RefObject } from "react";
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
	Globe,
	Library,
	Loader2,
	Plus,
	RefreshCw,
} from "lucide-react";
import { CollapsibleNoteText } from "@/components/CollapsibleNoteText";
import { uploadFromUrlWithFallback } from "@/lib/uploadUtils";
import { citationPreviewAtom } from "../atoms";
import { toScrollerContent, visibleBounds } from "../visibleBounds";
import { refreshReference, useResolvedReference, type ResolvedReference } from "./resolve";

const CARD_WIDTH = 540;
const GAP = 12;
const VIEWPORT_MARGIN = 8;
/** Room to want below the anchor before the card's real height is known. */
const ESTIMATED_HEIGHT = 300;
const MIN_HEIGHT = 80;

interface CardPosition {
	left: number;
	top: number;
	width: number;
	above: boolean;
	maxHeight: number;
}

/**
 * Prefer a 540px card below the anchor with a 12px gap, flipping above when
 * the other side has more room; clamp horizontally inside the *visible* part
 * of the scroller. Returned in the scroller's content coordinates (the portal
 * target), so a horizontally scrolled, zoomed page is accounted for.
 */
function computePosition(
	anchorRect: DOMRect,
	scroller: HTMLElement,
	cardHeight: number | null,
	lockedAbove: boolean | null
): CardPosition {
	const vis = visibleBounds(scroller);
	const width = Math.max(0, Math.min(CARD_WIDTH, vis.right - vis.left - VIEWPORT_MARGIN * 2));
	let left = anchorRect.left + anchorRect.width / 2 - width / 2;
	left = Math.max(
		vis.left + VIEWPORT_MARGIN,
		Math.min(left, vis.right - width - VIEWPORT_MARGIN)
	);

	const spaceBelow = vis.bottom - anchorRect.bottom - GAP - VIEWPORT_MARGIN;
	const spaceAbove = anchorRect.top - vis.top - GAP - VIEWPORT_MARGIN;
	const needed = Math.max(cardHeight ?? 0, ESTIMATED_HEIGHT);
	const above = lockedAbove ?? (spaceBelow < needed && spaceAbove > spaceBelow);
	const content = toScrollerContent(
		scroller,
		left,
		above ? anchorRect.top - GAP : anchorRect.bottom + GAP
	);
	return {
		left: content.left,
		top: content.top,
		width,
		above,
		maxHeight: Math.max(MIN_HEIGHT, above ? spaceAbove : spaceBelow),
	};
}

const ABSTRACT_CLASS = "mt-1 text-xs leading-relaxed text-muted-foreground break-words";
const URL_RE = /(?:https?:\/\/|www\.)[^\s<>"]+/gi;

function hostOf(url: string): string {
	try {
		return new URL(url).hostname.replace(/^www\./, "");
	} catch {
		return url;
	}
}

function authorLine(authors: string[] | undefined): string | null {
	if (!authors?.length) return null;
	return authors.slice(0, 6).join(", ") + (authors.length > 6 ? " et al." : "");
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

/** DOI / arXiv / landing page / PDF links for a resolved paper. */
function PaperLinks({ reference }: { reference: ResolvedReference }) {
	const links: { label: string; href: string }[] = [];
	const doiHref = reference.doi ? `https://doi.org/${reference.doi}` : null;
	const arxivHref = reference.arxiv_id ? `https://arxiv.org/abs/${reference.arxiv_id}` : null;
	if (doiHref) links.push({ label: `DOI ${reference.doi}`, href: doiHref });
	if (arxivHref) links.push({ label: `arXiv:${reference.arxiv_id}`, href: arxivHref });
	if (reference.url && reference.url !== doiHref && reference.url !== arxivHref) {
		links.push({ label: hostOf(reference.url), href: reference.url });
	}
	if (reference.pdf_url) links.push({ label: "PDF", href: reference.pdf_url });
	if (!links.length) return null;
	return (
		<div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px]">
			{links.map((link) => (
				<a
					key={link.href}
					href={link.href}
					target="_blank"
					rel="noopener noreferrer"
					className="max-w-full truncate text-blue-500 hover:underline"
				>
					{link.label}
				</a>
			))}
		</div>
	);
}

function PaperBody({ reference }: { reference: ResolvedReference }) {
	const libraryId = reference.kind === "library" ? reference.library_paper_id : null;
	const meta = [reference.venue, reference.year].filter(Boolean).join(" · ");
	const authors = authorLine(reference.authors);
	return (
		<div className="flex gap-4 p-4">
			<Thumbnail previewUrl={reference.preview_url} inLibrary={Boolean(libraryId)} />
			<div className="min-w-0 flex-1">
				{libraryId && (
					<Link
						href={`/paper/${libraryId}`}
						className="mb-1 flex w-fit items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-blue-500 hover:underline"
					>
						<Library className="size-3" /> In your library
					</Link>
				)}
				<p className="line-clamp-2 text-sm font-semibold leading-snug">
					{reference.title}
				</p>
				{authors && <p className="mt-0.5 truncate text-xs text-muted-foreground">{authors}</p>}
				{meta && <p className="mt-0.5 truncate text-[11px] text-muted-foreground">{meta}</p>}
				{reference.abstract && (
					<CollapsibleNoteText
						key={reference.abstract}
						content={reference.abstract}
						paragraphClassName={ABSTRACT_CLASS}
					/>
				)}
				<PaperLinks reference={reference} />
			</div>
		</div>
	);
}

function WebBody({ reference }: { reference: ResolvedReference }) {
	const [imageFailed, setImageFailed] = useState(false);
	const byline = [authorLine(reference.authors), reference.year].filter(Boolean).join(" · ");
	const showImage = reference.image_url && !imageFailed;
	return (
		<div className="flex gap-4 p-4">
			{showImage ? (
				<div className="h-[68px] w-[120px] shrink-0 overflow-hidden rounded-lg border border-border bg-muted">
					{/* eslint-disable-next-line @next/next/no-img-element */}
					<img
						src={reference.image_url!}
						alt=""
						className="h-full w-full object-cover"
						loading="lazy"
						referrerPolicy="no-referrer"
						onError={() => setImageFailed(true)}
					/>
				</div>
			) : (
				<div className="flex size-[68px] shrink-0 items-center justify-center rounded-lg border border-border bg-muted">
					<Globe className="size-7 text-muted-foreground" />
				</div>
			)}
			<div className="min-w-0 flex-1">
				<p className="mb-1 flex items-center gap-1.5 truncate text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
					<Globe className="size-3 shrink-0" /> {reference.site_name ?? (reference.url ? hostOf(reference.url) : "Web page")}
				</p>
				<p className="line-clamp-2 text-sm font-semibold leading-snug">{reference.title}</p>
				{byline && <p className="mt-0.5 truncate text-xs text-muted-foreground">{byline}</p>}
				{reference.abstract && (
					<CollapsibleNoteText
						key={reference.abstract}
						content={reference.abstract}
						paragraphClassName={ABSTRACT_CLASS}
					/>
				)}
				{reference.url && (
					<a
						href={reference.url}
						target="_blank"
						rel="noopener noreferrer"
						className="mt-1.5 block truncate text-[11px] text-blue-500 hover:underline"
					>
						{reference.url.replace(/^https?:\/\//, "")}
					</a>
				)}
			</div>
		</div>
	);
}

/** Reference text with its URLs clickable. */
function Linkified({ text }: { text: string }) {
	const parts: ReactNode[] = [];
	let last = 0;
	for (const match of text.matchAll(URL_RE)) {
		const url = match[0].replace(/[.,;:)\]]+$/, "");
		const start = match.index ?? 0;
		parts.push(text.slice(last, start));
		parts.push(
			<a
				key={start}
				href={url.startsWith("www.") ? `https://${url}` : url}
				target="_blank"
				rel="noopener noreferrer"
				className="text-blue-500 hover:underline"
			>
				{url}
			</a>
		);
		last = start + url.length;
	}
	parts.push(text.slice(last));
	return <>{parts}</>;
}

function RawBody({
	referenceText,
	status,
	repairedUrl,
	onRetry,
}: {
	referenceText: string;
	status: ReactNode;
	repairedUrl?: string | null;
	onRetry?: () => void;
}) {
	// The server may have repaired a URL the PDF broke across lines.
	const showRepaired = repairedUrl && !referenceText.includes(repairedUrl);
	return (
		<div className="p-4">
			<p className="mb-1.5 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
				<BookOpen className="size-3" /> Reference
			</p>
			<p className="text-xs leading-relaxed text-foreground break-words">
				<Linkified text={referenceText} />
			</p>
			{showRepaired && (
				<a
					href={repairedUrl}
					target="_blank"
					rel="noopener noreferrer"
					className="mt-1.5 block truncate text-[11px] text-blue-500 hover:underline"
				>
					{repairedUrl}
				</a>
			)}
			{(status || onRetry) && (
				<div className="mt-2 flex items-center gap-3 text-xs text-muted-foreground" role="status">
					{status}
					{onRetry && (
						<button
							onClick={onRetry}
							className="flex items-center gap-1 font-medium hover:text-foreground"
						>
							<RefreshCw className="size-3" /> Look up again
						</button>
					)}
				</div>
			)}
		</div>
	);
}

/**
 * Hover/click preview for a citation.
 *
 * States: still extracting the entry (skeleton), extracted entry (resolved on
 * the server and shown as a paper, a web page or the raw reference text), and
 * no readable entry. Extraction failure never starts a lookup.
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
	const [pos, setPos] = useState<CardPosition | null>(null);
	const cardRef = useRef<HTMLDivElement>(null);
	const contentRef = useRef<HTMLDivElement>(null);
	const [cardHeight, setCardHeight] = useState<number | null>(null);
	// Above vs. below is decided once per anchor: the card grows as the
	// reference resolves, and flipping sides mid-read would be jarring.
	const decided = useRef<{ anchor: DOMRect; above: boolean } | null>(null);
	const [importing, setImporting] = useState(false);
	const [imported, setImported] = useState(false);
	const [refreshing, setRefreshing] = useState(false);
	const referenceText = preview?.state === "entry" ? preview.referenceText : null;
	const { data: reference, error: lookupError } = useResolvedReference(referenceText);

	const anchorRect = preview?.anchorRect ?? null;
	useLayoutEffect(() => {
		const scroller = scrollerRef.current;
		if (!anchorRect || !scroller) {
			decided.current = null;
			setPos(null);
			return;
		}
		const locked =
			decided.current?.anchor === anchorRect && cardHeight != null
				? decided.current.above
				: null;
		const next = computePosition(anchorRect, scroller, cardHeight, locked);
		if (cardHeight != null) decided.current = { anchor: anchorRect, above: next.above };
		setPos(next);
	}, [anchorRect, cardHeight, scrollerRef]);

	// The card's natural height (its content can grow past maxHeight and
	// scroll): skeleton, raw reference and resolved paper all differ.
	const mounted = Boolean(preview && pos);
	useLayoutEffect(() => {
		const card = cardRef.current;
		const content = contentRef.current;
		if (!mounted || !card || !content) {
			setCardHeight(null);
			return;
		}
		const read = () => setCardHeight(card.scrollHeight);
		read();
		const observer = new ResizeObserver(read);
		observer.observe(content);
		return () => observer.disconnect();
	}, [mounted]);

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
		transform: pos.above ? "translateY(-100%)" : undefined,
		maxHeight: pos.maxHeight,
		overflowY: "auto",
		transition: "opacity 100ms ease-out",
	};

	const destinationPage =
		preview.state !== "skeleton"
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

	const resolved = preview.state === "entry" ? reference : undefined;
	const importUrl = resolved?.kind === "paper" ? resolved.pdf_url : null;
	const libraryId = resolved?.kind === "library" ? resolved.library_paper_id : null;
	const openTarget = libraryId
		? `/paper/${libraryId}`
		: resolved && resolved.kind !== "unresolved"
			? resolved.url
			: null;

	const addToLibrary = async () => {
		if (!importUrl || importing) return;
		setImporting(true);
		try {
			const uploaded = await uploadFromUrlWithFallback(importUrl);
			setImported(true);
			toast.success("Added to your library — processing in the background.", {
				action: {
					label: "Open",
					onClick: () => window.open(`/paper/${uploaded.paperId}`, "_blank"),
				},
			});
		} catch (error) {
			console.error("Failed to import cited paper:", error);
			toast.error(
				error instanceof Error ? error.message : "Could not add that paper."
			);
		} finally {
			setImporting(false);
		}
	};

	const lookUpAgain = async (text: string) => {
		setRefreshing(true);
		try {
			await refreshReference(text);
		} catch {
			toast.error("Reference lookup unavailable right now.");
		} finally {
			setRefreshing(false);
		}
	};

	let body: ReactNode = null;
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
		case "entry":
			if (resolved?.kind === "library" || resolved?.kind === "paper") {
				body = <PaperBody reference={resolved} />;
			} else if (resolved?.kind === "web") {
				body = <WebBody key={resolved.url ?? resolved.title} reference={resolved} />;
			} else {
				const status = refreshing || (!resolved && !lookupError)
					? "Looking up reference…"
					: lookupError
						? "Reference lookup unavailable. The reference is still available above."
						: null;
				body = (
					<RawBody
						referenceText={preview.referenceText}
						status={status}
						repairedUrl={resolved?.url}
						onRetry={resolved && !refreshing ? () => void lookUpAgain(preview.referenceText) : undefined}
					/>
				);
			}
			break;
		case "unavailable":
			body = (
				<div className="flex items-center gap-3 p-4">
					<ExternalLink className="size-4 shrink-0 text-muted-foreground" />
					<p className="text-xs text-muted-foreground">
						Reference text unavailable — could not read this citation’s bibliography entry.
					</p>
				</div>
			);
			break;
	}

	const hasFooter = Boolean(jumpButton || openTarget || importUrl);

	return createPortal(
		<div
			ref={cardRef}
			data-citation-preview
			data-citation-state={resolved ? resolved.kind : preview.state}
			role="region"
			aria-label="Citation preview"
			style={style}
			className="z-20 overflow-hidden rounded-xl border border-border bg-popover shadow-lg"
		>
			<div ref={contentRef}>
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
								(libraryId ? (
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
			</div>
		</div>,
		scrollerRef.current ?? document.body
	);
}
