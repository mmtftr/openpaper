"use client";

import { useCallback } from "react";
import useSWR from "swr";
import { api, unwrap } from "@/lib/api/client";
import type { SupplementaryMaterialSummary } from "@/lib/schema";
import { parentPaperIdAtom } from "./paperStore";
import { usePaperAtomValue } from "./PaperStoreProvider";

const EMPTY: SupplementaryMaterialSummary[] = [];

/** The parent paper's supplementaries (one SWR entry, shared by the reader and markdown views). */
export function useSupplementaryMaterials() {
    const parentPaperId = usePaperAtomValue(parentPaperIdAtom);
    const { data, mutate } = useSWR(
        parentPaperId ? ["/api/paper/{paper_id}/supplementary", parentPaperId] : null,
        ([, paperId]) =>
            unwrap(
                api.GET("/api/paper/{paper_id}/supplementary", {
                    params: { path: { paper_id: paperId } },
                })
            ),
        { onError: (err) => console.error("Error fetching supplementary materials:", err) }
    );
    const refetch = useCallback(async () => {
        await mutate();
    }, [mutate]);
    return { materials: data ?? EMPTY, refetch };
}
