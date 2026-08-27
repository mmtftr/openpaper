"use client";

import { useEffect } from "react";
import type { RefObject } from "react";
import { useSetAtom } from "jotai";
import type { PDFDocumentProxy, PDFViewer } from "../pdfjs";
import { citationPreviewAtom } from "../atoms";
import { extractBibEntry } from "./bibliography";
import {
	extractArxivIdFromUrl,
	isNonCitationDest,
	parseCiteHref,
	resolveDestination,
} from "./helpers";
import { resolvePaper } from "./resolve";

const HOVER_DEBOUNCE_MS = 120;
const DISMISS_DELAY_MS = 150;

function safeDecode(href: string): string {
	try {
		return decodeURIComponent(href);
	} catch {
		return href;
	}
}

/** Only internal citation links (`#cite.*`) get hover previews. */
function citationAnchor(target: EventTarget | null): HTMLAnchorElement | null {
	if (!(target instanceof Element)) return null;
	const anchor = target.closest(".annotationLayer a[href]");
	if (!(anchor instanceof HTMLAnchorElement)) return null;
	const href = safeDecode(anchor.getAttribute("href") ?? "");
	return href.startsWith("#cite.") && !isNonCitationDest(href) ? anchor : null;
}

/**
 * Reference resolution, as an event-delegation layer over pdf.js's annotation
 * layer.
 *
 * The clickable reference boxes are not something this code draws — they are
 * the PDF's own link annotations, which pdf.js renders as
 * `<section class="linkAnnotation"><a href="#cite.foo">`. All we add is
 * behaviour: hover (120ms debounce) and click resolve the link's destination,
 * pull the bibliography entry text off that page, and look the reference up.
 *
 * Non-citation internal links (figures, tables, equations) fall through to
 * pdf.js's own navigation, and external links are marked for the styling in
 * globals.css rather than being followed silently.
 */
export function useCitationLinks(
	containerRef: RefObject<HTMLDivElement | null>,
	viewer: PDFViewer | null,
	pdfDoc: PDFDocumentProxy | null,
	onJumpToPage?: (page: number) => void,
	/** Record the current scroll position before following an in-document link. */
	onBeforeNavigate?: () => void
) {
	const setPreview = useSetAtom(citationPreviewAtom);

	useEffect(() => {
		const container = containerRef.current;
		if (!container || !viewer || !pdfDoc) return;

		let generation = 0;
		let hoverTimer: ReturnType<typeof setTimeout> | undefined;
		let dismissTimer: ReturnType<typeof setTimeout> | undefined;
		// Aborts the in-flight lookup when the pointer moves on, so sweeping
		// across a row of citations doesn't leave a queue of dead requests.
		let inFlight: AbortController | undefined;

		const clearTimers = () => {
			if (hoverTimer) clearTimeout(hoverTimer);
			if (dismissTimer) clearTimeout(dismissTimer);
			hoverTimer = undefined;
			dismissTimer = undefined;
		};

		const dismiss = () => {
			generation++;
			clearTimers();
			inFlight?.abort();
			inFlight = undefined;
			setPreview(null);
		};

		const resolveFor = async (anchor: HTMLAnchorElement, myGen: number) => {
			const href = safeDecode(anchor.getAttribute("href") ?? "");
			const cite = parseCiteHref(href);
			const anchorRect = anchor.getBoundingClientRect();
			setPreview({ state: "skeleton", anchorRect });

			// 1. Resolve the in-document destination + bibliography entry.
			let destinationPage: number | null = null;
			let bibText: string | null = null;
			try {
				const dest = await resolveDestination(pdfDoc, href);
				if (dest) {
					destinationPage = dest.page;
					bibText = await extractBibEntry(
						pdfDoc,
						dest.page,
						dest.x,
						dest.y,
						cite?.author ?? null,
						cite?.year ?? null
					);
				}
			} catch {
				// destination failures just mean no jump target / raw text
			}
			if (myGen !== generation) return;

			const referenceText =
				bibText ||
				(anchor.textContent ?? "").trim() ||
				(cite?.name.replace(/[-_]/g, " ") ?? "");

			// 2. Try to match a real paper (own library first, then OpenAlex).
			inFlight?.abort();
			const controller = new AbortController();
			inFlight = controller;
			let outcome: Awaited<ReturnType<typeof resolvePaper>> = null;
			try {
				outcome = await resolvePaper(referenceText, controller.signal);
			} catch {
				outcome = controller.signal.aborted ? null : "unavailable";
			}
			if (controller.signal.aborted) return;
			if (myGen !== generation) return;

			if (outcome === "unavailable") {
				setPreview({ state: "unavailable", anchorRect });
			} else if (outcome) {
				setPreview({
					state: "paper",
					anchorRect,
					paper: outcome.paper,
					destinationPage,
				});
			} else {
				setPreview({ state: "raw", anchorRect, referenceText, destinationPage });
			}
		};

		const onOver = (e: MouseEvent) => {
			const anchor = citationAnchor(e.target);
			if (!anchor) return;
			if (dismissTimer) {
				clearTimeout(dismissTimer);
				dismissTimer = undefined;
			}
			if (hoverTimer) clearTimeout(hoverTimer);
			const myGen = ++generation;
			hoverTimer = setTimeout(() => {
				hoverTimer = undefined;
				void resolveFor(anchor, myGen);
			}, HOVER_DEBOUNCE_MS);
		};

		const onOut = (e: MouseEvent) => {
			if (!citationAnchor(e.target)) return;
			if (dismissTimer) clearTimeout(dismissTimer);
			dismissTimer = setTimeout(() => {
				dismissTimer = undefined;
				// Keep the card if the pointer moved onto another citation link or
				// onto the preview card itself.
				const hovered = document.querySelectorAll(":hover");
				let keep = false;
				for (const el of hovered) {
					if (
						el instanceof Element &&
						(el.matches(".annotationLayer a[href]") ||
							el.closest("[data-citation-preview]"))
					) {
						keep = true;
						break;
					}
				}
				if (!keep) dismiss();
			}, DISMISS_DELAY_MS);
		};

		const onClickCapture = (e: MouseEvent) => {
			if (!(e.target instanceof Element)) return;
			const anchor = e.target.closest(".annotationLayer a[href]");
			if (!(anchor instanceof HTMLAnchorElement)) return;
			const hrefRaw = anchor.getAttribute("href") ?? "";
			const href = safeDecode(hrefRaw);

			// External links: let the browser handle navigation, but preview the
			// paper when the target is an arXiv abstract we can resolve.
			if (!href.startsWith("#")) {
				const externalId = extractArxivIdFromUrl(hrefRaw);
				if (externalId) {
					const rect = anchor.getBoundingClientRect();
					const myGen = ++generation;
					setPreview({ state: "skeleton", anchorRect: rect });
					void resolvePaper(`arXiv:${externalId}`)
						.then((outcome) => {
							if (myGen !== generation) return;
							if (outcome && outcome !== "unavailable") {
								setPreview({
									state: "paper",
									anchorRect: rect,
									paper: outcome.paper,
									destinationPage: null,
								});
							} else if (outcome === "unavailable") {
								setPreview({ state: "unavailable", anchorRect: rect });
							} else {
								setPreview({
									state: "raw",
									anchorRect: rect,
									referenceText: `arXiv:${externalId}`,
									destinationPage: null,
								});
							}
						})
						.catch(() => {
							if (myGen === generation)
								setPreview({ state: "unavailable", anchorRect: rect });
						});
				}
				return;
			}

			e.preventDefault();

			// Non-citation internal links (figures etc.) fall through to pdf.js nav.
			if (!parseCiteHref(href)) {
				const name = href.slice(1);
				// Figures, tables and equations are jumps the reader will want to
				// come back from just as much as a citation.
				onBeforeNavigate?.();
				void Promise.resolve(
					viewer.linkService?.goToDestination?.(name as never)
				);
				return;
			}

			const myGen = ++generation;
			void resolveFor(anchor, myGen);
		};

		const onScroll = () => dismiss();

		const onDocClick = (e: MouseEvent) => {
			if (!(e.target instanceof Element)) return;
			if (e.target.closest(".annotationLayer a[href]")) return;
			if (e.target.closest("[data-citation-preview]")) return;
			dismiss();
		};

		// Preserve openpaper's external-link affordance (styled in globals.css):
		// uploaded PDFs are untrusted, so links leaving the document are marked.
		const markExternalLinks = () => {
			container
				.querySelectorAll<HTMLAnchorElement>(".annotationLayer a[href]")
				.forEach((a) => {
					const href = a.getAttribute("href") ?? "";
					if (href.startsWith("#")) return;
					// Bail if already marked. Writing the same attribute value still
					// produces a mutation record, and this runs *from* a
					// MutationObserver on the same subtree — without this guard each
					// pass would schedule the next one indefinitely.
					if (a.dataset.externalLink === "true") return;
					a.dataset.externalLink = "true";
					a.setAttribute("rel", "noopener noreferrer nofollow");
					a.setAttribute("target", "_blank");
				});
		};
		markExternalLinks();
		const linkObserver = new MutationObserver(markExternalLinks);
		linkObserver.observe(container, { childList: true, subtree: true });

		const onJump = (e: Event) => {
			const page = (e as CustomEvent<{ page: number }>).detail?.page;
			if (page) onJumpToPage?.(page);
		};

		container.addEventListener("mouseover", onOver);
		container.addEventListener("mouseout", onOut);
		container.addEventListener("click", onClickCapture, { capture: true });
		container.addEventListener("scroll", onScroll, { passive: true });
		document.addEventListener("click", onDocClick);
		window.addEventListener("reader-citation-jump", onJump);

		return () => {
			generation++;
			clearTimers();
			inFlight?.abort();
			linkObserver.disconnect();
			container.removeEventListener("mouseover", onOver);
			container.removeEventListener("mouseout", onOut);
			container.removeEventListener("click", onClickCapture, { capture: true });
			container.removeEventListener("scroll", onScroll);
			document.removeEventListener("click", onDocClick);
			window.removeEventListener("reader-citation-jump", onJump);
			setPreview(null);
		};
	}, [containerRef, viewer, pdfDoc, setPreview, onJumpToPage, onBeforeNavigate]);
}
