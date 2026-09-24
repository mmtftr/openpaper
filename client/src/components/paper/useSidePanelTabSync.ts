"use client";

import { useEffect, useRef } from "react";
import { useSearchParams } from "next/navigation";
import {
    activeHighlightAtom,
    paperAtom,
    SIDE_PANEL_TOOLS,
    sidePanelTabAtom,
    userMessageReferencesAtom,
    type SidePanelTab,
} from "./paperStore";
import { usePaperAtom, usePaperAtomValue } from "./PaperStoreProvider";
import { replaceUrl } from "./usePaperLoader";

/** The tab an `rsf` URL param asks for (`focus` is the old name of `read`). */
function tabFromRsf(rsf: string | null): SidePanelTab {
    if (rsf === "read" || rsf === "focus") return "Read";
    return SIDE_PANEL_TOOLS.find((tool) => tool.toLowerCase() === rsf) ?? "Chat";
}

/**
 * The side-panel tab's page-level rules:
 *
 * - once the paper has loaded, open the tab named by the URL's `rsf` param
 *   (read on first render only), then mirror every tab change back into `rsf`;
 * - selecting an AI highlight opens Annotations (its thread lives there);
 * - staging a chat reference opens Chat.
 */
export function useSidePanelTabSync() {
    const searchParams = useSearchParams();
    const initialRsfRef = useRef<string | null | undefined>(undefined);
    if (initialRsfRef.current === undefined) {
        initialRsfRef.current = searchParams.get("rsf")?.toLowerCase() || null;
    }

    const paper = usePaperAtomValue(paperAtom);
    const [tab, setTab] = usePaperAtom(sidePanelTabAtom);
    const initializedRef = useRef(false);

    useEffect(() => {
        if (!paper || initializedRef.current) return;
        initializedRef.current = true;
        setTab(tabFromRsf(initialRsfRef.current ?? null));
    }, [paper, setTab]);

    useEffect(() => {
        // Only once the tab has been restored from the original `rsf`.
        if (!initializedRef.current) return;
        const params = new URLSearchParams(window.location.search);
        params.set("rsf", tab.toLowerCase());
        replaceUrl(`${window.location.pathname}?${params.toString()}`);
    }, [tab]);

    const activeHighlight = usePaperAtomValue(activeHighlightAtom);
    useEffect(() => {
        if (activeHighlight?.role === "assistant") setTab("Annotations");
    }, [activeHighlight, setTab]);

    const references = usePaperAtomValue(userMessageReferencesAtom);
    useEffect(() => {
        if (references.length > 0) setTab("Chat");
    }, [references, setTab]);
}
