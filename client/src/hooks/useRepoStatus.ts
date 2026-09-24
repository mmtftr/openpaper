"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import useSWR from "swr";

import {
    clearRepoFileCache,
    connectPaperRepo,
    disconnectPaperRepo,
    getPaperRepo,
    type PaperRepo,
} from "@/lib/repoApi";

/**
 * The paper's connected code repo, polled while ingestion is in flight.
 *
 * `repo === null` means "no repo connected" (the endpoint 404s); `error` is
 * reserved for real failures so the UI can tell the two apart.
 *
 * Cached per paper by SWR: a response for a paper the reader has switched
 * away from lands in that paper's cache entry, never in the current one, and
 * a poll that started before a connect / disconnect is discarded by SWR's
 * mutation ordering.
 */

const POLL_INTERVAL_MS = 3000;

export interface UseRepoStatus {
    repo: PaperRepo | null;
    loading: boolean;
    /** Failure of the status fetch itself, not an ingestion error. */
    error: string | null;
    /** A connect/disconnect call is in flight. */
    mutating: boolean;
    refresh: () => Promise<void>;
    connect: (url: string) => Promise<void>;
    disconnect: () => Promise<void>;
}

const messageOf = (error: unknown): string =>
    error instanceof Error ? error.message : "Something went wrong";

export function useRepoStatus(paperId: string | null | undefined): UseRepoStatus {
    const { data, error, isLoading, mutate } = useSWR(
        paperId ? ["/api/paper/{paper_id}/repo", paperId] : null,
        ([, id]: [string, string]) => getPaperRepo(id)
    );
    const [mutating, setMutating] = useState(false);
    const mountedRef = useRef(true);

    useEffect(() => {
        mountedRef.current = true;
        return () => {
            mountedRef.current = false;
        };
    }, []);

    // Poll only while the server is still working on the snapshot. `status`
    // is a primitive dep, so the interval survives refreshes that don't
    // change it. (SWR's own `refreshInterval` is only re-read on a timer tick,
    // so a connect from the "no repo" state would never start it.)
    const status = data?.status;
    useEffect(() => {
        if (status !== "pending" && status !== "ingesting") return;
        const timer = setInterval(() => {
            mutate();
        }, POLL_INTERVAL_MS);
        return () => clearInterval(timer);
    }, [status, mutate]);

    const refresh = useCallback(async () => {
        await mutate();
    }, [mutate]);

    const connect = useCallback(
        async (url: string) => {
            if (!paperId) return;
            setMutating(true);
            // A new snapshot invalidates any file cached from the old one.
            clearRepoFileCache(paperId);
            try {
                // The freshly created row supersedes any poll already in flight.
                await mutate(connectPaperRepo(paperId, url), { revalidate: false });
            } finally {
                if (mountedRef.current) setMutating(false);
            }
        },
        [paperId, mutate]
    );

    const disconnect = useCallback(async () => {
        if (!paperId) return;
        setMutating(true);
        clearRepoFileCache(paperId);
        try {
            await mutate(
                disconnectPaperRepo(paperId).then(() => null),
                { revalidate: false }
            );
        } finally {
            if (mountedRef.current) setMutating(false);
        }
    }, [paperId, mutate]);

    return {
        repo: data ?? null,
        loading: isLoading,
        error: error ? messageOf(error) : null,
        mutating,
        refresh,
        connect,
        disconnect,
    };
}
