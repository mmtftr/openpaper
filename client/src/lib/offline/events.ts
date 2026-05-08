export const OFFLINE_SYNC_STATE_EVENT = "openpaper:offline-sync-state";

export function notifyOfflineSyncStateChanged() {
    if (typeof window === "undefined") return;
    window.dispatchEvent(new CustomEvent(OFFLINE_SYNC_STATE_EVENT));
}
