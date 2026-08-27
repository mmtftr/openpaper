"use client";

import { useEffect, useState } from "react";
import { useAtomValue } from "jotai";
import { ChevronDown, ChevronRight, List } from "lucide-react";
import type { PDFDocumentProxy } from "./pdfjs";
import { pdfDocAtom } from "./atoms";
import { viewerApiAtom } from "./useViewer";

interface OutlineItem {
	title: string;
	bold?: boolean;
	italic?: boolean;
	dest: string | unknown[] | null;
	url?: string;
	items?: OutlineItem[];
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
	onGoToPage: (page: number) => void;
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

/** Document outline (PDF bookmarks), from `doc.getOutline()`. */
export default function Outline() {
	const doc = useAtomValue(pdfDocAtom);
	const api = useAtomValue(viewerApiAtom);
	const [items, setItems] = useState<OutlineItem[] | null>(null);

	useEffect(() => {
		let cancelled = false;
		setItems(null);
		if (!doc) return;
		doc
			.getOutline()
			.then((outline) => {
				if (!cancelled) setItems((outline ?? []) as OutlineItem[]);
			})
			.catch(() => {
				if (!cancelled) setItems([]);
			});
		return () => {
			cancelled = true;
		};
	}, [doc]);

	if (!items)
		return <p className="p-4 text-xs text-muted-foreground">Loading outline…</p>;
	if (items.length === 0)
		return (
			<div className="flex flex-col items-center gap-2 p-6 text-muted-foreground">
				<List className="size-6" />
				<p className="text-xs">No outline</p>
			</div>
		);

	return (
		<div className="p-2">
			{items.map((item, i) => (
				<Row
					key={i}
					item={item}
					depth={0}
					doc={doc}
					onGoToPage={(page) => api?.goToPage(page)}
				/>
			))}
		</div>
	);
}
