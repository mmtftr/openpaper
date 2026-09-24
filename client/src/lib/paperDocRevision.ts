import { atom, getDefaultStore } from "jotai";

/**
 * Per-paper count of chat-agent doc writes (`write_doc` tool results) seen in
 * this browser session. The chat bumps it as the tool result streams in;
 * `PaperDocEditor` watches its paper's entry and refetches the doc list and
 * the open doc when it moves. Lives in jotai's default store: the chat stream
 * can outlive the chat panel (it keeps running across side-panel tabs), so the
 * bump can't depend on either component being mounted.
 */
export const agentDocWritesAtom = atom<Record<string, number>>({});

export function bumpAgentDocWrites(paperId: string) {
    getDefaultStore().set(agentDocWritesAtom, (prev) => ({
        ...prev,
        [paperId]: (prev[paperId] ?? 0) + 1,
    }));
}
