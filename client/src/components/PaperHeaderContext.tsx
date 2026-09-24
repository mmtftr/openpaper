"use client";

import { createContext, useCallback, useContext, useMemo, useState, ReactNode } from "react";
import { toast } from "sonner";
import { api, unwrap } from "@/lib/api/client";
import { PaperStatus, PaperStatusEnum } from "@/components/utils/PdfStatus";

type PaperHeaderContextValue = {
    paperId: string | null;
    paperStatus: PaperStatus | null;
    paperTitle: string | null;
    setPaperContext: (paperId: string | null, status: PaperStatus | null, title?: string | null) => void;
    updatePaperStatus: (status: PaperStatus) => Promise<void>;
};

const PaperHeaderContext = createContext<PaperHeaderContextValue | null>(null);

export function PaperHeaderProvider({ children }: { children: ReactNode }) {
    const [paperId, setPaperId] = useState<string | null>(null);
    const [paperStatus, setPaperStatus] = useState<PaperStatus | null>(null);
    const [paperTitle, setPaperTitle] = useState<string | null>(null);

    const setPaperContext = useCallback(
        (nextId: string | null, status: PaperStatus | null, title: string | null = null) => {
            setPaperId(nextId);
            setPaperStatus(status);
            setPaperTitle(title);
        },
        [],
    );

    const updatePaperStatus = useCallback(
        async (status: PaperStatus) => {
            if (!paperId) return;
            const previous = paperStatus;
            setPaperStatus(status);
            try {
                await unwrap(api.POST("/api/paper/status", {
                    params: { query: { status, paper_id: paperId } },
                }));
                if (status === PaperStatusEnum.COMPLETED) {
                    toast.success("Completed reading! 🎉", {
                        description: paperTitle ? `Congrats on finishing ${paperTitle}!` : undefined,
                        duration: 5000,
                    });
                }
            } catch (error) {
                console.error("Error updating paper status:", error);
                setPaperStatus(previous);
                toast.error("Failed to update paper status.");
            }
        },
        [paperId, paperStatus, paperTitle],
    );

    const value = useMemo<PaperHeaderContextValue>(
        () => ({ paperId, paperStatus, paperTitle, setPaperContext, updatePaperStatus }),
        [paperId, paperStatus, paperTitle, setPaperContext, updatePaperStatus],
    );

    return <PaperHeaderContext.Provider value={value}>{children}</PaperHeaderContext.Provider>;
}

export function usePaperHeader() {
    return useContext(PaperHeaderContext);
}
