"use client";

import { AlertCircle, CheckCircle2, CloudOff, Loader2, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "@/components/ui/popover";
import { useSyncState } from "@/lib/offline";

function formatRelativeTime(value?: string) {
    if (!value) return "Never";
    const deltaSeconds = Math.max(1, Math.round((Date.now() - Date.parse(value)) / 1000));
    if (deltaSeconds < 60) return `${deltaSeconds}s ago`;
    const deltaMinutes = Math.round(deltaSeconds / 60);
    if (deltaMinutes < 60) return `${deltaMinutes}m ago`;
    const deltaHours = Math.round(deltaMinutes / 60);
    if (deltaHours < 24) return `${deltaHours}h ago`;
    return `${Math.round(deltaHours / 24)}d ago`;
}

function formatBytes(bytes: number) {
    if (bytes < 1024) return `${bytes} B`;
    const kb = bytes / 1024;
    if (kb < 1024) return `${kb.toFixed(1)} KB`;
    return `${(kb / 1024).toFixed(1)} MB`;
}

export function SyncIndicator() {
    const { state, pdfBytes, online, syncNow } = useSyncState();

    if (!state) return null;

    const hasPending = state.pendingCount > 0;
    const label = !online
        ? hasPending
            ? `${state.pendingCount} pending`
            : "Offline"
        : state.status === "syncing"
            ? "Syncing..."
            : state.status === "paused"
                ? "Paused - sign in"
                : state.status === "needs-attention" || state.status === "error"
                    ? "Needs attention"
                    : hasPending
                        ? `${state.pendingCount} pending`
                        : state.scope === "latest-50"
                            ? `Latest 50 synced ${formatRelativeTime(state.lastSuccessfulSyncAt)}`
                            : "Notes and papers synced";

    const Icon = state.status === "syncing"
        ? Loader2
        : !online
            ? CloudOff
            : state.status === "needs-attention" || state.status === "error"
                ? AlertCircle
                : CheckCircle2;

    const tone = !online || hasPending
        ? "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300"
        : state.status === "needs-attention" || state.status === "error"
            ? "border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300"
            : "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300";

    return (
        <Popover>
            <PopoverTrigger asChild>
                <button
                    type="button"
                    className={`inline-flex max-w-[220px] items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium ${tone}`}
                    aria-label="Open sync status"
                >
                    <Icon className={`h-3.5 w-3.5 shrink-0 ${state.status === "syncing" ? "animate-spin" : ""}`} />
                    <span className="truncate">{label}</span>
                </button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-80">
                <div className="space-y-3">
                    <div>
                        <div className="text-sm font-medium">Offline sync</div>
                        <div className="text-xs text-muted-foreground">
                            {state.scope === "latest-50" ? "Latest 50 papers plus pinned papers" : "All papers"}
                        </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2 text-xs">
                        <div className="text-muted-foreground">Last sync</div>
                        <div className="text-right">{formatRelativeTime(state.lastSuccessfulSyncAt)}</div>
                        <div className="text-muted-foreground">Synced papers</div>
                        <div className="text-right">{state.syncedPaperIds.length}</div>
                        <div className="text-muted-foreground">Pinned offline</div>
                        <div className="text-right">{state.pinnedPaperIds.length}</div>
                        <div className="text-muted-foreground">Pending changes</div>
                        <div className="text-right">{state.pendingCount}</div>
                        <div className="text-muted-foreground">PDF cache</div>
                        <div className="text-right">{formatBytes(pdfBytes)}</div>
                    </div>
                    {state.lastError && (
                        <Badge variant="outline" className="w-full justify-start text-xs">
                            {state.lastError}
                        </Badge>
                    )}
                    <Button size="sm" variant="outline" className="w-full gap-2" onClick={syncNow}>
                        <RefreshCw className="h-3.5 w-3.5" />
                        Sync now
                    </Button>
                </div>
            </PopoverContent>
        </Popover>
    );
}
