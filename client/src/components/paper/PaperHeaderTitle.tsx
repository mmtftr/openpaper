"use client";

import { paperAtom } from "./paperStore";
import { usePaperAtomValue } from "./PaperStoreProvider";

/** The open paper's title in the app header (empty while it loads). */
export function PaperHeaderTitle() {
    const title = usePaperAtomValue(paperAtom)?.title;
    if (!title) return null;
    return (
        <span className="block truncate animate-in fade-in duration-300" title={title}>
            {title}
        </span>
    );
}
