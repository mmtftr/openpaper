"use client";

import { useEffect, useState } from "react";
import { useAtomValue } from "jotai";
import { ChevronDown, ChevronRight, List } from "lucide-react";
import type { PDFDocumentProxy } from "./pdfjs";
import { pdfDocAtom } from "./atoms";
import { viewerApiAtom } from "./useViewer";
import { api as apiClient, unwrap, type Schemas } from "@/lib/api/client";

function generatedItems(entries: Schemas["OutlineEntry"][]): OutlineItem[] {
	return entries.map((entry) => ({
		title: entry.title,
		dest: null,
		page: entry.page,
		topPercent: entry.top_percent ?? undefined,
		items: generatedItems(entry.children ?? []),
	}));
}

interface OutlineItem {
	title: string;
	bold?: boolean;
	italic?: boolean;
	dest: string | unknown[] | null;
	url?: string;
	items?: OutlineItem[];
	page?: number;
	topPercent?: number;
}

// pdf.js types the destination ref loosely; we only ever hand it back to
// getPageIndex, so a nominal alias keeps the casts honest.
type DestRef = Parameters<PDFDocumentProxy["getPageIndex"]>[0];

async function pageIndexFor(doc: PDFDocumentProxy, ref: DestRef) {
	const index = await doc.getPageIndex(ref);
	return index + 1;
}

async function resolveDest(
	doc: PDFDocumentProxy,
	dest: string | unknown[] | null
) {
	if (typeof dest === "string") {
		const explicit = await doc.getDestination(dest);
		if (!explicit) return null;
		return pageIndexFor(doc, explicit[0] as DestRef);
	}
	if (Array.isArray(dest)) return pageIndexFor(doc, dest[0] as DestRef);
	return null;
}

function Row({
	item,
	depth,
	doc,
	onGoToPage,
}: {
	item: OutlineItem;
	depth: number;
	doc: PDFDocumentProxy | null;
	onGoToPage: (page: number, topPercent?: number) => void;
}) {
	const [open, setOpen] = useState(depth < 1);
	const hasChildren = !!item.items?.length;

	return (
		<div>
			<div
				className="flex items-center gap-0.5 rounded-md pr-1 hover:bg-muted"
				style={{ paddingLeft: depth * 12 }}
			>
				{hasChildren ? (
					<button
						onClick={() => setOpen(!open)}
						aria-label={open ? "Collapse section" : "Expand section"}
						className="flex size-5 shrink-0 items-center justify-center text-muted-foreground hover:text-foreground"
					>
						{open ? (
							<ChevronDown className="size-3.5" />
						) : (
							<ChevronRight className="size-3.5" />
						)}
					</button>
				) : (
					<span className="w-5 shrink-0" />
				)}
				<button
					onClick={async () => {
						if (item.page !== undefined) {
							onGoToPage(item.page, item.topPercent);
							return;
						}
						if (item.url) {
							window.open(item.url, "_blank", "noopener,noreferrer");
							return;
						}
						if (!doc) return;
						const page = await resolveDest(doc, item.dest);
						if (page) onGoToPage(page);
					}}
					className={`min-w-0 flex-1 truncate py-1 text-left text-xs hover:text-foreground ${
						item.bold ? "font-semibold text-foreground" : "text-muted-foreground"
					} ${item.italic ? "italic" : ""}`}
					title={item.title}
				>
					{item.title}
				</button>
			</div>
			{hasChildren && open && (
				<div>
					{item.items!.map((child, i) => (
						<Row
							key={i}
							item={child}
							depth={depth + 1}
							doc={doc}
							onGoToPage={onGoToPage}
						/>
					))}
				</div>
			)}
		</div>
	);
}

/** Prefer embedded bookmarks; generate an OCR outline only when none exist. */
export default function Outline({ displayedPaperId }: { displayedPaperId: string }) {
	const doc = useAtomValue(pdfDocAtom);
	const api = useAtomValue(viewerApiAtom);
	const [items, setItems] = useState<OutlineItem[] | null>(null);
	const [generated, setGenerated] = useState(false);
	const [error, setError] = useState(false);
	const [attempt, setAttempt] = useState(0);

	useEffect(() => {
		let cancelled = false;
		const controller = new AbortController();
		setItems(null);
		setGenerated(false);
		setError(false);
		if (!doc) return;
		doc
			.getOutline()
			.then(async (outline) => {
				if (cancelled) return;
				if (outline?.length || !displayedPaperId) {
					setItems((outline ?? []) as OutlineItem[]);
					return;
				}
				setGenerated(true);
				const entries = await unwrap(
					apiClient.GET("/api/paper/outline", {
						params: { query: { id: displayedPaperId } },
						signal: controller.signal,
					})
				);
				if (!cancelled) setItems(generatedItems(entries));
			})
			.catch(() => {
				if (!cancelled) setError(true);
			});
		return () => {
			cancelled = true;
			controller.abort();
		};
	}, [doc, displayedPaperId, attempt]);

	if (error)
		return (
			<div className="p-4 text-xs text-muted-foreground" role="status">
				<p>Couldn’t load outline.</p>
				<button className="mt-2 underline" onClick={() => setAttempt(attempt + 1)}>
					Try again
				</button>
			</div>
		);

	if (!items)
		return (
			<p className="p-4 text-xs text-muted-foreground" role="status">
				{generated ? "Generating outline…" : "Loading outline…"}
			</p>
		);
	if (items.length === 0)
		return (
			<div className="flex flex-col items-center gap-2 p-6 text-muted-foreground">
				<List className="size-6" />
				<p className="text-xs">No outline</p>
			</div>
		);

	return (
		<div className="p-2">
			{generated && (
				<p className="px-2 pb-2 text-[10px] text-muted-foreground">AI-generated</p>
			)}
			{items.map((item, i) => (
				<Row
					key={i}
					item={item}
					depth={0}
					doc={doc}
					onGoToPage={(page, topPercent) => {
						if (topPercent !== undefined) api?.goToPagePercent(page, topPercent);
						else api?.goToPage(page);
					}}
				/>
			))}
		</div>
	);
}
