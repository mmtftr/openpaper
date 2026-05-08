"use client";

import { useEffect } from "react";

import { usePapers } from "@/hooks/usePapers";
import { refreshSyncScope } from "@/lib/offline";

export function SyncScopeCoordinator() {
    const { papers } = usePapers();

    useEffect(() => {
        if (!papers) return;
        refreshSyncScope(papers).catch((error) => {
            console.error("Could not refresh offline sync scope", error);
        });
    }, [papers]);

    return null;
}
