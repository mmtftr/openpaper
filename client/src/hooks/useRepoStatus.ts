"use client";

import { useCallback, useEffect, useRef, useState } from "react";

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
    const [repo, setRepo] = useState<PaperRepo | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [mutating, setMutating] = useState(false);
    const inFlightRef = useRef(false);
    const mountedRef = useRef(true);
    // The paper the hook currently serves: a connect/disconnect that resolves
    // after a paper switch must not write the OLD paper's repo into state.
    const paperIdRef = useRef(paperId);
    paperIdRef.current = paperId;
    // Bumped whenever newer state supersedes in-flight reads (paper switch,
    // connect, disconnect).
    const epochRef = useRef(0);

    useEffect(() => {
        mountedRef.current = true;
        return () => {
            mountedRef.current = false;
        };
    }, []);

    const refresh = useCallback(async () => {
        if (!paperId || inFlightRef.current) return;
        inFlightRef.current = true;
        // A poll started for the previous paper (or before a connect landed)
        // must never write its answer over newer state.
        const epoch = epochRef.current;
        try {
            const result = await getPaperRepo(paperId);
            if (!mountedRef.current || epoch !== epochRef.current) return;
            setRepo(result);
            setError(null);
        } catch (err) {
            if (!mountedRef.current || epoch !== epochRef.current) return;
            setError(messageOf(err));
        } finally {
            inFlightRef.current = false;
            if (mountedRef.current && epoch === epochRef.current) {
                setLoading(false);
            }
        }
    }, [paperId]);

    useEffect(() => {
        // New paper: invalidate anything in flight and start clean.
        epochRef.current += 1;
        inFlightRef.current = false;
        setRepo(null);
        setError(null);
        if (!paperId) {
            setLoading(false);
            return;
        }
        setLoading(true);
        refresh();
    }, [paperId, refresh]);

    // Poll only while the server is still working on the snapshot. `status` is
    // a primitive dep, so the interval survives refreshes that don't change it.
    const status = repo?.status;
    useEffect(() => {
        if (status !== "pending" && status !== "ingesting") return;
        const timer = setInterval(() => {
            refresh();
        }, POLL_INTERVAL_MS);
        return () => clearInterval(timer);
    }, [status, refresh]);

    const connect = useCallback(
        async (url: string) => {
            if (!paperId) return;
            setMutating(true);
            // The freshly created row supersedes any poll already in flight,
            // and a new snapshot invalidates any file cached from the old one.
            epochRef.current += 1;
            clearRepoFileCache(paperId);
            try {
                const created = await connectPaperRepo(paperId, url);
                if (mountedRef.current && paperIdRef.current === paperId) {
                    epochRef.current += 1;
                    setRepo(created);
                    setError(null);
                }
            } finally {
                if (mountedRef.current) setMutating(false);
            }
        },
        [paperId]
    );

    const disconnect = useCallback(async () => {
        if (!paperId) return;
        setMutating(true);
        epochRef.current += 1;
        clearRepoFileCache(paperId);
        try {
            await disconnectPaperRepo(paperId);
            if (mountedRef.current && paperIdRef.current === paperId) {
                epochRef.current += 1;
                setRepo(null);
                setError(null);
            }
        } finally {
            if (mountedRef.current) setMutating(false);
        }
    }, [paperId]);

    return { repo, loading, error, mutating, refresh, connect, disconnect };
}
