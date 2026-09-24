"use client";

import { useEffect } from "react";
import type { RefObject } from "react";
import { useSetAtom } from "jotai";
import type { PDFDocumentProxy, PDFViewer } from "../pdfjs";
import { citationPreviewAtom } from "../atoms";
import { extractAllEntries, extractBibEntry } from "./bibliography";
import { decodeCitationHref, parseCiteHref, resolveDestination } from "./helpers";
import { prefetchReferences } from "./resolve";

const HOVER_DEBOUNCE_MS = 120;
const DISMISS_DELAY_MS = 250;
const PREFETCH_DELAY_MS = 2000;

function citationAnchor(target: EventTarget | null): HTMLAnchorElement | null {
  if (!(target instanceof Element)) return null;
  const anchor = target.closest(".annotationLayer a[href]");
  return anchor instanceof HTMLAnchorElement && parseCiteHref(anchor.getAttribute("href") ?? "") ? anchor : null;
}
function card(target: EventTarget | null): Element | null {
  return target instanceof Element ? target.closest("[data-citation-preview]") : null;
}

/** Event delegation on real PDF annotations, including publisher bibliography links. */
export function useCitationLinks(
  containerRef: RefObject<HTMLDivElement | null>, viewer: PDFViewer | null,
  pdfDoc: PDFDocumentProxy | null, onJumpToPage?: (page: number) => void,
  onBeforeNavigate?: () => void,
) {
  const setPreview = useSetAtom(citationPreviewAtom);
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !viewer || !pdfDoc) return;
    let generation = 0;
    let hoverTimer: ReturnType<typeof setTimeout> | undefined;
    let dismissTimer: ReturnType<typeof setTimeout> | undefined;
    let active: HTMLAnchorElement | null = null;
    let showing = false;
    const entries = new Map<string, Promise<{ referenceText: string | null; destinationPage: number | null }>>();
    const cancelDismiss = () => { clearTimeout(dismissTimer); dismissTimer = undefined; };
    const dismiss = () => {
      generation++;
      clearTimeout(hoverTimer); hoverTimer = undefined;
      cancelDismiss();
      active = null; showing = false; setPreview(null);
    };
    // Warm the whole bibliography once the document is idle, so hovers are
    // instant (one batch request per chunk; the server caches the answers).
    const prefetch = new AbortController();
    const prefetchTimer = setTimeout(() => {
      void extractAllEntries(pdfDoc, prefetch.signal)
        .then(texts => prefetchReferences(texts, prefetch.signal))
        .catch(() => {});
    }, PREFETCH_DELAY_MS);
    const scheduleDismiss = () => {
      cancelDismiss();
      dismissTimer = setTimeout(dismiss, DISMISS_DELAY_MS);
    };
    const entryFor = (anchor: HTMLAnchorElement) => {
      const rawHref = anchor.getAttribute("href") ?? "";
      const href = decodeCitationHref(rawHref);
      let entry = entries.get(href);
      if (!entry) {
        entry = (async () => {
          const cite = parseCiteHref(href);
          const destination = await resolveDestination(pdfDoc, rawHref);
          const text = destination ? await extractBibEntry(pdfDoc, destination.page, destination.x, destination.y, cite?.author ?? null, cite?.year ?? null, href.slice(1)) : null;
          return { referenceText: text, destinationPage: destination?.page ?? null };
        })();
        entries.set(href, entry);
        entry.catch(() => entries.delete(href));
      }
      return entry;
    };
    const resolveFor = async (anchor: HTMLAnchorElement, myGen: number) => {
      if (myGen !== generation) return;
      const anchorRect = anchor.getBoundingClientRect();
      // Keep the previous content during the very short adjacent-link extraction.
      // It is replaced atomically, without a blank/skeleton flash.
      if (!showing) { setPreview({ state: "skeleton", anchorRect }); showing = true; }
      let entry;
      try { entry = await entryFor(anchor); }
      catch { entry = { referenceText: null, destinationPage: null }; }
      if (myGen !== generation) return;
      const { referenceText, destinationPage } = entry;
      if (!referenceText?.trim()) {
        setPreview({ state: "unavailable", anchorRect, destinationPage });
        return; // Labels, destination keys and error messages are not references.
      }
      // The card resolves the entry itself (SWR-cached by text), showing the
      // raw reference until the answer arrives.
      setPreview({ state: "entry", anchorRect, referenceText, destinationPage });
    };
    const activate = (anchor: HTMLAnchorElement, immediate = false) => {
      cancelDismiss();
      if (active === anchor && (hoverTimer || showing) && !immediate) return;
      clearTimeout(hoverTimer);
      active = anchor;
      const myGen = ++generation;
      if (immediate) void resolveFor(anchor, myGen);
      else hoverTimer = setTimeout(() => { hoverTimer = undefined; void resolveFor(anchor, myGen); }, HOVER_DEBOUNCE_MS);
    };
    const onOver = (event: MouseEvent) => {
      if (card(event.target)) { cancelDismiss(); return; }
      const anchor = citationAnchor(event.target);
      if (anchor && !anchor.contains(event.relatedTarget as Node | null)) activate(anchor);
    };
    const onOut = (event: MouseEvent) => {
      const anchor = citationAnchor(event.target);
      const preview = card(event.target);
      if (!anchor && !preview) return;
      if ((anchor ?? preview)?.contains(event.relatedTarget as Node | null)) return;
      // A pointer sweep that ends before debounce should never open a card.
      if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = undefined; active = null; generation++; }
      if (card(event.relatedTarget) || citationAnchor(event.relatedTarget)) return;
      scheduleDismiss();
    };
    const onClick = (event: MouseEvent) => {
      if (!(event.target instanceof Element)) return;
      const anchor = event.target.closest(".annotationLayer a[href]");
      if (!(anchor instanceof HTMLAnchorElement)) return;
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      if (citationAnchor(anchor)) {
        // preventDefault alone does not stop pdf.js's onclick navigation.
        event.preventDefault(); event.stopPropagation();
        activate(anchor, true);
      } else if ((anchor.getAttribute("href") ?? "").startsWith("#")) {
        dismiss(); onBeforeNavigate?.(); // pdf.js owns the actual navigation.
      }
    };
    const onDocClick = (event: MouseEvent) => {
      // React may replace the clicked icon with a spinner before this bubbles
      // to document. The original event path still contains the card.
      if (!event.composedPath().some(target => card(target) || citationAnchor(target))) dismiss();
    };
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") dismiss(); };
    const onFocus = (event: FocusEvent) => {
      if (card(event.target)) cancelDismiss();
      const anchor = citationAnchor(event.target);
      if (anchor) activate(anchor, true);
    };
    const onBlur = (event: FocusEvent) => {
      if (!card(event.relatedTarget) && !citationAnchor(event.relatedTarget)) scheduleDismiss();
    };
    const markExternalLinks = () => {
      container.querySelectorAll<HTMLAnchorElement>(".annotationLayer a[href]").forEach(anchor => {
        if ((anchor.getAttribute("href") ?? "").startsWith("#") || anchor.dataset.externalLink === "true") return;
        anchor.dataset.externalLink = "true";
        anchor.setAttribute("rel", "noopener noreferrer nofollow"); anchor.setAttribute("target", "_blank");
      });
    };
    markExternalLinks();
    const observer = new MutationObserver(markExternalLinks);
    observer.observe(container, { childList: true, subtree: true });
    const onJump = (event: Event) => {
      const page = (event as CustomEvent<{page:number}>).detail?.page;
      if (page) { dismiss(); onJumpToPage?.(page); }
    };
    container.addEventListener("mouseover", onOver);
    container.addEventListener("mouseout", onOut);
    container.addEventListener("focusin", onFocus);
    container.addEventListener("focusout", onBlur);
    container.addEventListener("click", onClick, true);
    container.addEventListener("scroll", dismiss, { passive: true });
    document.addEventListener("click", onDocClick);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", dismiss);
    window.addEventListener("reader-citation-jump", onJump);
    return () => {
      dismiss(); observer.disconnect();
      clearTimeout(prefetchTimer); prefetch.abort();
      container.removeEventListener("mouseover", onOver);
      container.removeEventListener("mouseout", onOut);
      container.removeEventListener("focusin", onFocus);
      container.removeEventListener("focusout", onBlur);
      container.removeEventListener("click", onClick, true);
      container.removeEventListener("scroll", dismiss);
      document.removeEventListener("click", onDocClick);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("reader-citation-jump", onJump);
    };
  }, [containerRef, viewer, pdfDoc, setPreview, onJumpToPage, onBeforeNavigate]);
}
