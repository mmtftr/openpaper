"use client";

import { jumpToTextAtom, userMessageReferencesAtom } from "@/components/paper/paperStore";
import { usePaperAtom, useSetPaperAtom } from "@/components/paper/PaperStoreProvider";

/** The references staged for the next message: click one to find it in the PDF, × to drop it. */
export function PendingReferences() {
    const [references, setReferences] = usePaperAtom(userMessageReferencesAtom);
    const jumpToText = useSetPaperAtom(jumpToTextAtom);
    if (references.length === 0) return null;
    return (
        <div className="flex flex-wrap gap-1.5">
            {references.map((ref, index) => (
                <div
                    key={index}
                    className="group flex items-start gap-1 max-w-[260px] rounded-md border border-border/60 bg-muted/40 px-2 py-1"
                >
                    <button
                        type="button"
                        onClick={() => jumpToText(ref)}
                        className="text-xs text-muted-foreground line-clamp-2 text-left flex-1 hover:text-foreground"
                    >
                        {ref}
                    </button>
                    <button
                        type="button"
                        onClick={() => setReferences((prev) => prev.filter((_, i) => i !== index))}
                        className="text-muted-foreground/70 hover:text-foreground shrink-0 text-xs leading-none px-0.5"
                        aria-label="Remove reference"
                    >
                        ×
                    </button>
                </div>
            ))}
        </div>
    );
}
