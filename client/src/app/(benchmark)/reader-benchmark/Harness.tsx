"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { PdfReader, type HighlightJumpRequest } from "@/components/reader";
import { AnnotationsView } from "@/components/AnnotationsView";
import type { PaperHighlight, PaperHighlightAnnotation } from "@/lib/schema";
import type { TextAnchor, RenderedHighlightPosition } from "@/components/reader/types";

interface Fixture {
	id: string;
	title: string;
	highlights: PaperHighlight[];
	annotations: PaperHighlightAnnotation[];
}

// Only this opt-in dev harness delays extraction. Rendering uses pdf.js's
// streamTextContent, so real toolbar/sidebar navigation remains available.
async function holdPageText(paperId: string, pageNumber: number, signal: AbortSignal, percentOutline: boolean) {
	const { pdfjsLib, PDF_DOCUMENT_OPTIONS } = await import("@/components/reader/pdfjs");
	const task = pdfjsLib.getDocument({ url: `/reader-benchmark/assets/${paperId}.pdf`, ...PDF_DOCUMENT_OPTIONS });
	try {
		const doc = await task.promise;
		const page = await doc.getPage(pageNumber);
		if (signal.aborted) return;
		const prototype = Object.getPrototypeOf(page) as typeof page;
		const original = prototype.getTextContent;
		const docPrototype = Object.getPrototypeOf(doc) as typeof doc;
		const originalOutline = docPrototype.getOutline;
		// Replay a generated-outline destination through the real Outline row,
		// without invoking the backend's outline generation/cache write endpoint.
		if (percentOutline) docPrototype.getOutline = async function () {
			const items = await originalOutline.call(this);
			return items?.length ? [{ ...items[0], title: "Benchmark page-percent destination", dest: null, page: 2, topPercent: 40, items: [] }] : items;
		};
		let release!: () => void;
		const gate = new Promise<void>(resolve => { release = resolve; });
		const control = { blocked: 0, released: false, release: () => { control.released = true; release(); } };
		prototype.getTextContent = async function (...args) {
			if (this.pageNumber === pageNumber && !control.released) {
				control.blocked++;
				await gate;
			}
			return original.apply(this, args);
		};
		signal.addEventListener("abort", () => {
			control.release(); prototype.getTextContent = original; docPrototype.getOutline = originalOutline;
		}, { once: true });
		return control;
	} finally { await task.destroy(); }
}

// The audit uses the reader's actual anchor functions, in a separate document
// AFTER the click trials. It cannot warm the reader's document/index caches.
async function audit(paper: Fixture) {
	const { pdfjsLib, PDF_DOCUMENT_OPTIONS } = await import("@/components/reader/pdfjs");
	const { anchorFromScaledPosition, locateQuote } = await import("@/components/reader/anchoring");
	const task = pdfjsLib.getDocument({ url: `/reader-benchmark/assets/${paper.id}.pdf`, ...PDF_DOCUMENT_OPTIONS });
	const doc = await task.promise;
	try {
		const rows = [];
		for (const h of paper.highlights) {
			const stored = h.position ? anchorFromScaledPosition(h.position, h.raw_text) : null;
			const start = performance.now();
			const found = await locateQuote(doc, h.raw_text, h.page_number);
			const text: TextAnchor | null = found ? { ...found, quote: h.raw_text } : null;
			rows.push({ id: h.id, stored, text, anchor: stored ?? text, resolutionMs: performance.now() - start });
		}
		return rows;
	} finally {
		await task.destroy();
	}
}

declare global {
	interface Window {
		annotationBenchmark?: {
			audit: () => ReturnType<typeof audit>; cite: (term: string) => void;
			remount: () => void; refreshUrl: () => void;
			delay?: Awaited<ReturnType<typeof holdPageText>>;
			clicks: { id?: string; time: number; unresolved: boolean }[];
		};
	}
}

export default function Harness() {
	const [paper, setPaper] = useState<Fixture | null>(null);
	const [active, setActive] = useState<PaperHighlight | null>(null);
	const [jump, setJump] = useState<HighlightJumpRequest | null>(null);
	const [term, setTerm] = useState<string>();
	const search = useMemo(() => (term ? { term, nonce: 0 } : null), [term]);
	const [positions, setPositions] = useState(new Map<string, RenderedHighlightPosition>());
	const [readerMount, setReaderMount] = useState(0);
	const [urlVersion, setUrlVersion] = useState(0);
	const delay = useRef<Awaited<ReturnType<typeof holdPageText>>>(undefined);
	// `?writable`: notes are editable, kept in memory, so the popover and panel
	// composers (reply / edit / delete) can be exercised without a backend.
	const [writable, setWritable] = useState(false);
	const [notes, setNotes] = useState<PaperHighlightAnnotation[]>([]);
	useEffect(() => {
		const controller = new AbortController();
		const params = new URLSearchParams(location.search);
		const id = params.get("paper");
		setWritable(params.has("writable"));
		void fetch("/reader-benchmark/assets/manifest.json", { signal: controller.signal }).then(r => r.json()).then(async data => {
			const found: Fixture = data.papers.find((p: Fixture) => p.id === id) ?? data.papers[0];
			if (params.has("hold-text-page")) delay.current = await holdPageText(found.id, Number(params.get("hold-text-page")), controller.signal, params.has("percent-outline"));
			if (controller.signal.aborted) return;
			setPaper(found);
			setNotes(found.annotations);
		}).catch(error => { if (!controller.signal.aborted) console.error(error); });
		return () => controller.abort();
	}, []);
	const noteApi = writable && paper ? {
		addAnnotation: async (highlightId: string, content: string) => {
			const note: PaperHighlightAnnotation = {
				id: `local-${crypto.randomUUID()}`, highlight_id: highlightId, paper_id: paper.id,
				content, role: "user", created_at: new Date().toISOString(),
			};
			setNotes(prev => [...prev, note]);
			return note;
		},
		updateAnnotation: (annotationId: string, content: string) => {
			setNotes(prev => prev.map(n => n.id === annotationId ? { ...n, content } : n));
		},
		removeAnnotation: (annotationId: string) => {
			setNotes(prev => prev.filter(n => n.id !== annotationId));
		},
	} : {};
	useEffect(() => {
		if (!paper) return;
		window.annotationBenchmark = {
			audit: () => audit(paper), cite: term => { setJump(null); setTerm(term); }, clicks: [], delay: delay.current,
			remount: () => setReaderMount(n => n + 1), refreshUrl: () => setUrlVersion(n => n + 1),
		};
		return () => { delete window.annotationBenchmark; };
	}, [paper]);
	if (!paper) return <div>Loading fixtures…</div>;
	return (
		<main className="flex h-screen overflow-hidden">
			<div className="min-w-0 flex-1" data-benchmark-reader>
				<PdfReader key={readerMount} pdfUrl={`/reader-benchmark/assets/${paper.id}.pdf?version=${urlVersion}`} highlights={paper.highlights}
					annotations={notes} activeHighlight={active} setActiveHighlight={setActive} {...noteApi}
					highlightJumpRequest={jump} explicitSearch={search} onOverlaysCreated={setPositions} />
			</div>
			<aside className="w-96 shrink-0 overflow-hidden" data-benchmark-panel>
				<AnnotationsView highlights={paper.highlights} annotations={notes} {...noteApi}
					user={{ name: "Benchmark", picture: "" }} readonly={!writable} activeHighlight={active}
					renderedHighlightPositions={positions} onHighlightClick={highlight => {
						window.annotationBenchmark?.clicks.push({ id: highlight.id, time: performance.now(), unresolved: !positions.has(highlight.id!) });
						setActive(highlight);
						if (new URLSearchParams(location.search).has("legacy")) {
							// Retained for comparing the old panel action with new anchoring.
							setTerm(highlight.raw_text && !highlight.position ? highlight.raw_text : undefined);
						} else {
							setTerm(undefined);
							setJump(previous => ({ highlightId: highlight.id!, nonce: (previous?.nonce ?? 0) + 1 }));
						}
					}} />
			</aside>
		</main>
	);
}
