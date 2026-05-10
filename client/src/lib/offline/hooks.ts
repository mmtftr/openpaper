"use client";

import { useCallback, useEffect, useState } from "react";

import { getDefaultSyncState } from "./db";
import { OFFLINE_SYNC_STATE_EVENT } from "./events";
import { getPdfCacheBytes, prewarmAllSyncedPapers } from "./paperCache";
import { replayOutbox } from "./replay";
import type { SyncState } from "./types";

export function useOnlineStatus() {
    // Read navigator.onLine eagerly so the first render reflects reality —
    // otherwise the page flashes "online" for one paint when the user opens
    // an offline tab, briefly mis-routing the PDF viewer to the network URL.
    const [online, setOnline] = useState(() =>
        typeof navigator !== "undefined" ? navigator.onLine : true
    );

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
        // Drain queued offline writes first so the prewarm overwrites a clean
        // server state instead of trampling the user's pending changes.
        await replayOutbox();
        // Then refresh every paper in the sync set into IDB. This is what the
        // user expects when they click "Sync now" before boarding a plane.
        await prewarmAllSyncedPapers();
    }, []);

    return { state, pdfBytes, online: useOnlineStatus(), refresh, syncNow };
}
