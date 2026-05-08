"use client";

import { useCallback, useEffect, useState } from "react";

import { getDefaultSyncState } from "./db";
import { OFFLINE_SYNC_STATE_EVENT } from "./events";
import { getPdfCacheBytes } from "./paperCache";
import { replayOutbox } from "./replay";
import type { SyncState } from "./types";

export function useOnlineStatus() {
    const [online, setOnline] = useState(true);

    useEffect(() => {
        const update = () => setOnline(navigator.onLine);
        update();
        window.addEventListener("online", update);
        window.addEventListener("offline", update);
        return () => {
            window.removeEventListener("online", update);
            window.removeEventListener("offline", update);
        };
    }, []);

    return online;
}

export function useSyncState() {
    const [state, setState] = useState<SyncState | null>(null);
    const [pdfBytes, setPdfBytes] = useState(0);

    const refresh = useCallback(async () => {
        try {
            setState(await getDefaultSyncState());
            setPdfBytes(await getPdfCacheBytes());
        } catch (error) {
            console.error("Could not load offline sync state", error);
        }
    }, []);

    useEffect(() => {
        refresh();
        window.addEventListener(OFFLINE_SYNC_STATE_EVENT, refresh);
        window.addEventListener("online", refresh);
        window.addEventListener("offline", refresh);
        return () => {
            window.removeEventListener(OFFLINE_SYNC_STATE_EVENT, refresh);
            window.removeEventListener("online", refresh);
            window.removeEventListener("offline", refresh);
        };
    }, [refresh]);

    const syncNow = useCallback(async () => {
        await replayOutbox();
    }, []);

    return { state, pdfBytes, online: useOnlineStatus(), refresh, syncNow };
}
