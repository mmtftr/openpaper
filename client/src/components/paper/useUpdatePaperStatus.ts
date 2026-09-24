"use client";

import { useCallback } from "react";
import { toast } from "sonner";
import { api, unwrap } from "@/lib/api/client";
import { PaperStatus, PaperStatusEnum } from "@/components/utils/PdfStatus";
import { paperAtom, parentPaperIdAtom } from "./paperStore";
import { usePaperStore } from "./PaperStoreProvider";

/** Set the (parent) paper's reading status: optimistic, rolled back if the request fails. */
export function useUpdatePaperStatus() {
    const store = usePaperStore();
    return useCallback(
        async (status: PaperStatus) => {
            const paperId = store.get(parentPaperIdAtom);
            const paper = store.get(paperAtom);
            if (!paperId || !paper) return;
            const previous = paper.status;
            const setStatus = (next: PaperStatus) =>
                store.set(paperAtom, (current) => (current ? { ...current, status: next } : current));
            setStatus(status);
            try {
                await unwrap(
                    api.POST("/api/paper/status", {
                        params: { query: { status, paper_id: paperId } },
                    })
                );
                if (status === PaperStatusEnum.COMPLETED) {
                    toast.success("Completed reading! 🎉", {
                        description: paper.title ? `Congrats on finishing ${paper.title}!` : undefined,
                        duration: 5000,
                    });
                }
            } catch (error) {
                console.error("Error updating paper status:", error);
                setStatus(previous);
                toast.error("Failed to update paper status.");
            }
        },
        [store]
    );
}
