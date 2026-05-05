/**
 * Tiny event channel between PaperChatPanel and PaperDocEditor.
 *
 * The editor needs to know when an agent turn is in flight so it can poll
 * `/api/document/main` and pick up `write_main_doc` results live. We don't
 * want a full pub-sub or context provider for this — both components live in
 * the same paper page, keyed by `paperId`, and the signal is just a boolean.
 */

type Listener = (streaming: boolean) => void;

const listeners = new Map<string, Set<Listener>>();
const state = new Map<string, boolean>();

export function setPaperChatStreaming(paperId: string, streaming: boolean) {
    state.set(paperId, streaming);
    const subs = listeners.get(paperId);
    if (subs) {
        for (const fn of subs) fn(streaming);
    }
}

export function subscribePaperChatStreaming(
    paperId: string,
    cb: Listener
): () => void {
    let subs = listeners.get(paperId);
    if (!subs) {
        subs = new Set();
        listeners.set(paperId, subs);
    }
    subs.add(cb);
    // Fire current state so the subscriber doesn't have to wait for the next
    // toggle to learn whether streaming is already active.
    cb(state.get(paperId) ?? false);
    return () => {
        const set = listeners.get(paperId);
        if (!set) return;
        set.delete(cb);
        if (set.size === 0) listeners.delete(paperId);
    };
}
