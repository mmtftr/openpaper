"use client";

import { atom, createStore } from "jotai";
import type { HighlightColor, PaperData, PaperHighlight } from "@/lib/schema";
import type {
    HighlightJumpRequest,
    RenderedHighlightPosition,
    TextSearchRequest,
} from "@/components/reader";

/**
 * State shared across the paper page: which paper and PDF are on screen,
 * the side-panel tab, highlight selection, chat references and citation
 * jumps. One store per route paper id (`PaperStoreProvider`), so opening
 * another paper starts from scratch.
 *
 * Read and written through `usePaperAtomValue` & co., which name the store
 * explicitly: the PDF reader mounts its own jotai `Provider` for viewer
 * state, and a plain `useAtom` below it would silently read that one.
 */

// ---- Which paper -----------------------------------------------------------

/** The id in the URL (a supplementary's id when one was opened directly). */
export const routePaperIdAtom = atom("");
/**
 * The paper the chat, notes and header bind to: the route id, or the parent
 * when the route id turned out to be a supplementary.
 */
export const parentPaperIdAtom = atom("");
/** The paper whose PDF (and highlights) is on screen: the parent or one of its supplementaries. */
export const displayedPaperIdAtom = atom("");

/** The parent paper; null until the first load finishes (or if it failed). */
export const paperAtom = atom<PaperData | null>(null);
export const paperLoadingAtom = atom(true);
/**
 * Details of the displayed paper when it is a supplementary. While another
 * one loads this still holds the previous one.
 */
export const supplementaryPaperAtom = atom<PaperData | null>(null);
/** Details of the paper whose PDF is on screen. */
export const displayedPaperAtom = atom((get) =>
    get(displayedPaperIdAtom) === get(parentPaperIdAtom)
        ? get(paperAtom)
        : get(supplementaryPaperAtom)
);

// ---- Layout ----------------------------------------------------------------

/** The side panel's tools, top to bottom in its toolbar. */
export const SIDE_PANEL_TOOLS = ["Chat", "Annotations", "Doc"] as const;
export type SidePanelTool = (typeof SIDE_PANEL_TOOLS)[number];
/** A tool, or "Read": the panel hidden (the reader's toolbar toggles it). */
export type SidePanelTab = SidePanelTool | "Read";
export type MobileView = "reader" | "markdown" | "panel";

const sidePanelTabBaseAtom = atom<SidePanelTab>("Chat");
/** The tab before "Read", restored when leaving read mode. */
const lastNonReadTabAtom = atom<SidePanelTab>("Chat");
/** The side panel's tab; "Read" hides the panel (read mode). */
export const sidePanelTabAtom = atom(
    (get) => get(sidePanelTabBaseAtom),
    (get, set, next: SidePanelTab) => {
        const previous = get(sidePanelTabBaseAtom);
        if (previous === next) return;
        if (previous !== "Read") set(lastNonReadTabAtom, previous);
        set(sidePanelTabBaseAtom, next);
    }
);
export const toggleReadModeAtom = atom(null, (get, set) => {
    set(
        sidePanelTabAtom,
        get(sidePanelTabBaseAtom) === "Read" ? get(lastNonReadTabAtom) : "Read"
    );
});

/** Which of reader / markdown / tools fills the screen on mobile. */
export const mobileViewAtom = atom<MobileView>("reader");

// ---- Highlights ------------------------------------------------------------

/**
 * Changes when ingest's AI highlights stage finishes for `paperId`; part of
 * the highlight/annotation SWR keys.
 */
export const highlightsRefreshAtom = atom({ paperId: "", key: "" });
export const activeHighlightAtom = atom<PaperHighlight | null>(null);
/** Scroll the reader to a highlight; a new nonce on every request. */
export const highlightJumpRequestAtom = atom<HighlightJumpRequest | null>(null);
/** Highlights the reader has drawn, by id (the panel uses it to tell anchored ones). */
export const renderedHighlightPositionsAtom = atom<Map<string, RenderedHighlightPosition>>(
    new Map()
);

/** A highlight picked in the side panel: select it and scroll the reader to it. */
export const jumpToHighlightAtom = atom(null, (_get, set, highlight: PaperHighlight) => {
    set(activeHighlightAtom, highlight);
    set(textSearchAtom, null);
    const highlightId = highlight.id;
    if (highlightId) {
        set(highlightJumpRequestAtom, (previous) => ({
            highlightId,
            nonce: (previous?.nonce ?? 0) + 1,
        }));
    }
});

/** Scroll the markdown (Text view) to a highlight's passage and mark it. */
export interface MarkdownJumpRequest {
    /** The paper whose markdown holds the passage. */
    paperId: string;
    highlight: PaperHighlight;
    /** The tint, as the highlights list shows it (AI highlights are purple). */
    color: HighlightColor;
    /** Where in the document the passage should be (from its page), for repeats. */
    expectedFraction: number | null;
    nonce: number;
}
/** Pending until the Text view has handled it (it mounts lazily, so it may be a while). */
export const markdownJumpRequestAtom = atom<MarkdownJumpRequest | null>(null);
const markdownJumpNonceAtom = atom(0);

/**
 * Phones: open a highlight in the Text view. Highlights without text (or,
 * via `markdownJumpSettledAtom`, whose passage the markdown lacks) open in
 * the PDF instead.
 */
export const openHighlightInTextAtom = atom(null, (get, set, highlight: PaperHighlight) => {
    if (!highlight.raw_text?.trim()) {
        set(jumpToHighlightAtom, highlight);
        set(mobileViewAtom, "reader");
        return;
    }
    set(activeHighlightAtom, highlight);
    const paperId = highlight.paper_id || get(displayedPaperIdAtom);
    if (paperId !== get(displayedPaperIdAtom)) set(displayedPaperIdAtom, paperId);
    const pageCount = get(displayedPaperAtom)?.page_count;
    const page = highlight.page_number;
    const nonce = get(markdownJumpNonceAtom) + 1;
    set(markdownJumpNonceAtom, nonce);
    set(markdownJumpRequestAtom, {
        paperId,
        highlight,
        color: highlight.role === "assistant" ? "purple" : highlight.color || "blue",
        expectedFraction: page && pageCount ? Math.min(1, (page - 0.5) / pageCount) : null,
        nonce,
    });
    set(mobileViewAtom, "markdown");
});

/** The Text view handled a jump; one it couldn't place goes to the PDF. */
export const markdownJumpSettledAtom = atom(null, (get, set, nonce: number, found: boolean) => {
    const request = get(markdownJumpRequestAtom);
    if (!request || request.nonce !== nonce) return;
    set(markdownJumpRequestAtom, null);
    if (found) return;
    set(jumpToHighlightAtom, request.highlight);
    set(mobileViewAtom, "reader");
});

// ---- Chat references and text search ---------------------------------------

/** Quotes staged for the next chat message (PDF selections, code snippets). */
export const userMessageReferencesAtom = atom<string[]>([]);

/**
 * Text the reader should find and scroll to (chat citations, attached
 * references). Every request is a fresh object, so the same citation clicked
 * twice searches again.
 */
export const textSearchAtom = atom<TextSearchRequest | null>(null);
const textSearchNonceAtom = atom(0);
export const jumpToTextAtom = atom(null, (get, set, term: string, page?: number) => {
    const nonce = get(textSearchNonceAtom) + 1;
    set(textSearchNonceAtom, nonce);
    set(textSearchAtom, { term, page, nonce });
});

// ---- Citation jumps --------------------------------------------------------

export interface CitationRef {
    key: string;
    messageIndex: number;
}

/** The chat citation last clicked; cleared after a few seconds. */
export const activeCitationAtom = atom<CitationRef | null>(null);
/** A clicked citation whose quote wasn't found in the PDF; flashed in its sources list. */
export const flashCitationAtom = atom<CitationRef | null>(null);
const pendingCitationSearchAtom = atom<(CitationRef & { term: string }) | null>(null);

const ACTIVE_CITATION_MS = 3000;
const FLASH_CITATION_MS = 2500;

export interface CitationClick extends CitationRef {
    /** The paper the citation quotes (the parent or a supplementary). */
    paperId: string;
    /** The quote to search for; null when its sources row isn't rendered. */
    term: string | null;
    /** Page the agent quoted from: a search hint. */
    page?: number;
}

/**
 * Show the cited paper's PDF and search it for the quote — the reader holds
 * the search until that PDF has loaded.
 */
export const clickCitationAtom = atom(null, (get, set, click: CitationClick) => {
    set(highlightJumpRequestAtom, null);
    const citation = { key: String(click.key), messageIndex: click.messageIndex };
    set(activeCitationAtom, citation);
    if (click.paperId !== get(displayedPaperIdAtom)) {
        set(displayedPaperIdAtom, click.paperId);
    }
    if (click.term !== null) {
        set(pendingCitationSearchAtom, { ...citation, term: click.term });
        // A re-click re-flashes if it again finds nothing.
        set(flashCitationAtom, null);
        set(jumpToTextAtom, click.term, click.page);
    }
    setTimeout(() => set(activeCitationAtom, null), ACTIVE_CITATION_MS);
});

/** The reader finished a search: flash the citation that started it if nothing matched. */
export const citationSearchSettledAtom = atom(
    null,
    (get, set, term: string, matchCount: number) => {
        const pending = get(pendingCitationSearchAtom);
        if (!pending || pending.term !== term) return;
        set(pendingCitationSearchAtom, null);
        if (matchCount !== 0) return;
        const flash = { key: pending.key, messageIndex: pending.messageIndex };
        set(flashCitationAtom, flash);
        setTimeout(() => {
            if (get(flashCitationAtom) === flash) set(flashCitationAtom, null);
        }, FLASH_CITATION_MS);
    }
);

// ---- Store -----------------------------------------------------------------

export type PaperStore = ReturnType<typeof createStore>;

/** A fresh store for the paper at `routePaperId`, showing `display` if the URL asks for it. */
export function createPaperStore(routePaperId: string, display: string | null): PaperStore {
    const store = createStore();
    store.set(routePaperIdAtom, routePaperId);
    store.set(parentPaperIdAtom, routePaperId);
    store.set(displayedPaperIdAtom, display || routePaperId);
    return store;
}
