import type { PDFViewer } from "./pdfjs";
import type { TextAnchor } from "./types";

/**
 * Jump using page geometry, including pages that have never been painted.
 * Keep correcting briefly: lazy page sizing, fit-width and pdf.js zoom can
 * change the target's offset after the first scroll. User input cancels this
 * correction so it cannot fight a subsequent manual scroll.
 */
export async function jumpToAnchor(
	viewer: PDFViewer,
	anchor: Pick<TextAnchor, "page" | "rects">,
	signal: AbortSignal
): Promise<void> {
	const doc = viewer.pdfDocument;
	if (!doc || signal.aborted) return;
	const pageNumber = Math.max(1, Math.min(doc.numPages, Math.round(anchor.page)));
	if (!Number.isFinite(pageNumber)) return;
	const rect = anchor.rects[0] ?? { left: 0, top: 0, width: 0, height: 0 };
	if (!Object.values(rect).every(Number.isFinite)) return;

	// Bound readiness too, not only the correction loop. A broken PDF must not
	// leave a pending jump around forever. The caller aborts on a newer request.
	await new Promise<void>((resolve) => {
		const container = viewer.container;
		let frame = 0;
		let correctingUntil = 0;
		let pagesReady = false;
		void viewer.pagesPromise?.then(() => { pagesReady = true; }).catch(() => { pagesReady = true; });
		let finished = false;
		const finish = () => {
			if (finished) return;
			finished = true;
			clearTimeout(timeout);
			cancelAnimationFrame(frame);
			signal.removeEventListener("abort", finish);
			resolve();
		};
		const timeout = setTimeout(finish, 2000);
		signal.addEventListener("abort", finish, { once: true });
		const correct = () => {
			if (finished || signal.aborted || viewer.pdfDocument !== doc) return finish();
			const page = viewer.getPageView(pageNumber - 1)?.div;
			if (page) {
				const box = page.getBoundingClientRect();
				const viewport = container.getBoundingClientRect();
				const top = box.top + box.height * rect.top / 100 - viewport.top;
				const left = box.left + box.width * rect.left / 100 - viewport.left;
				const width = box.width * rect.width / 100;
				const height = box.height * rect.height / 100;
				const inset = Math.min(container.clientHeight / 3, Math.max(16, (container.clientHeight - height) / 2));
				// The toolbar is outside this scroller. A third of its height gives
				// context above the quote; center horizontally for zoomed columns.
				container.scrollTo({
					top: Math.max(0, container.scrollTop + top - inset),
					left: Math.max(0, container.scrollLeft + left - Math.max(16, (container.clientWidth - width) / 2)),
					behavior: "instant",
				});
				if (!correctingUntil) correctingUntil = performance.now() + 600;
			}
			if (pagesReady && correctingUntil && performance.now() >= correctingUntil) return finish();
			frame = requestAnimationFrame(correct);
		};
		// Don't wait for every page in a long document. Load this page's real
		// dimensions, then let pdf.js render it as soon as scrolling exposes it.
		void Promise.all([viewer.firstPagePromise, doc.getPage(pageNumber)])
			.then(([, page]) => {
				if (finished || signal.aborted || viewer.pdfDocument !== doc) return;
				const view = viewer.getPageView(pageNumber - 1);
				if (view && !view.pdfPage) view.setPdfPage(page);
				correct();
			})
			.catch(finish);
	});
}
