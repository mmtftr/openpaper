"use client";

import { useEffect, useRef, useState } from "react";
import { useAtomValue } from "jotai";
import { currentPageAtom, numPagesAtom, pdfDocAtom } from "./atoms";
import { viewerApiAtom } from "./useViewer";

/**
 * Lazily-rendered page thumbnails. Pages render as their box scrolls within
 * 400px of the viewport, and every in-flight render task is cancelled when the
 * document changes so switching papers doesn't leak workers.
 */
export default function Thumbnails() {
	const doc = useAtomValue(pdfDocAtom);
	const numPages = useAtomValue(numPagesAtom);
	const current = useAtomValue(currentPageAtom);
	const api = useAtomValue(viewerApiAtom);

	const listRef = useRef<HTMLDivElement>(null);
	const tasks = useRef(new Set<{ cancel(): void }>());
	const [done, setDone] = useState<Set<number>>(new Set());
	const [ratios, setRatios] = useState<Map<number, number>>(new Map());

	useEffect(() => {
		setDone(new Set());
		setRatios(new Map());
		const pending = tasks.current;
		pending.forEach((t) => t.cancel());
		pending.clear();
	}, [doc]);

	useEffect(() => {
		const root = listRef.current;
		if (!root || !doc) return;
		let cancelled = false;

		async function render(page: number) {
			if (!doc) return;
			try {
				const pdfPage = await doc.getPage(page);
				if (cancelled) return;
				const base = pdfPage.getViewport({ scale: 1 });
				setRatios((m) => new Map(m).set(page, base.width / base.height));
				const box = root?.querySelector<HTMLElement>(`[data-thumb-box="${page}"]`);
				if (!box || box.querySelector("canvas")) {
					setDone((s) => new Set(s).add(page));
					return;
				}
				const width = box.clientWidth || 200;
				const viewport = pdfPage.getViewport({
					scale: (width * window.devicePixelRatio) / base.width,
				});
				const canvas = document.createElement("canvas");
				canvas.width = Math.floor(viewport.width);
				canvas.height = Math.floor(viewport.height);
				canvas.className = "thumbnail-canvas absolute inset-0 h-full w-full";
				const ctx = canvas.getContext("2d")!;
				const task = pdfPage.render({ canvas, canvasContext: ctx, viewport });
				tasks.current.add(task);
				await task.promise;
				tasks.current.delete(task);
				if (cancelled || !box.isConnected) return;
				box.appendChild(canvas);
				setDone((s) => new Set(s).add(page));
			} catch {
				/* render cancelled */
			}
		}

		const io = new IntersectionObserver(
			(entries) => {
				for (const entry of entries) {
					if (!entry.isIntersecting) continue;
					io.unobserve(entry.target);
					void render(Number((entry.target as HTMLElement).dataset.thumb));
				}
			},
			{ rootMargin: "400px" }
		);
		root.querySelectorAll<HTMLElement>("[data-thumb]").forEach((el) => {
			io.observe(el);
		});
		return () => {
			cancelled = true;
			io.disconnect();
		};
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [doc, numPages]);

	useEffect(() => {
		listRef.current
			?.querySelector(`[data-thumb="${current}"]`)
			?.scrollIntoView({ block: "nearest", behavior: "smooth" });
	}, [current]);

	return (
		<div ref={listRef} className="flex flex-col gap-3 p-3">
			{Array.from({ length: numPages }, (_, i) => i + 1).map((page) => {
				const ratio = ratios.get(page);
				const active = page === current;
				return (
					<button
						key={page}
						data-thumb={page}
						onClick={() => api?.goToPage(page)}
						aria-label={`Go to page ${page}`}
						aria-current={active ? "page" : undefined}
						className={`group mx-auto flex w-full flex-col items-center gap-1.5 rounded-lg p-1 transition-colors ${
							active ? "bg-blue-500/10" : "hover:bg-muted"
						}`}
					>
						<div className="relative w-full">
							<div
								data-thumb-box={page}
								className={`relative w-full overflow-hidden rounded-md border bg-white transition-shadow dark:bg-neutral-900 ${
									active
										? "border-blue-500 shadow-md ring-1 ring-blue-500"
										: "border-border group-hover:shadow-sm"
								}`}
								style={{ aspectRatio: ratio ? `${ratio}` : "0.707" }}
							/>
							{!done.has(page) && (
								<div className="pointer-events-none absolute inset-0 animate-pulse rounded-md bg-border/50" />
							)}
						</div>
						<span
							className={`text-[11px] tabular-nums ${
								active ? "font-semibold text-blue-500" : "text-muted-foreground"
							}`}
						>
							{page}
						</span>
					</button>
				);
			})}
		</div>
	);
}
