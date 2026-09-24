"use client";

import { useState } from "react";
import {
    AlertTriangle,
    Ban,
    CheckCircle2,
    Circle,
    CircleDashed,
    Clock,
    Loader2,
    RotateCw,
    WifiOff,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { parentPaperIdAtom } from "@/components/paper/paperStore";
import { usePaperAtomValue } from "@/components/paper/PaperStoreProvider";
import { cn } from "@/lib/utils";
import { retryInSeconds, useIngest, useNow, type IngestStage } from "@/hooks/useIngest";

// Stages whose progress counts pages.
const PAGE_STAGES = new Set(["ocr", "ocr_repair"]);

function StatusIcon({ stage }: { stage: IngestStage }) {
    const cls = "size-3.5 shrink-0";
    switch (stage.status) {
        case "succeeded":
            return <CheckCircle2 className={cn(cls, "text-green-600")} />;
        case "skipped":
            return <CircleDashed className={cn(cls, "text-muted-foreground")} />;
        case "running":
            return <Loader2 className={cn(cls, "animate-spin text-blue-500")} />;
        case "queued":
            return <Clock className={cn(cls, "text-muted-foreground")} />;
        case "failed":
            return <AlertTriangle className={cn(cls, "text-destructive")} />;
        case "blocked":
            return <Ban className={cn(cls, "text-muted-foreground")} />;
        default:
            return <Circle className={cn(cls, "text-muted-foreground")} />;
    }
}

function progressText(stage: IngestStage): string | null {
    if (!stage.progress_total) return null;
    const unit = PAGE_STAGES.has(stage.name) ? " pages" : "";
    return `${stage.progress_done ?? 0}/${stage.progress_total}${unit}`;
}

function statusText(stage: IngestStage, now: number): string {
    const retryIn = retryInSeconds(stage, now);
    if (retryIn !== null) return `retrying in ${retryIn} s (attempt ${stage.attempt + 1}/${stage.max_attempts})`;
    return stage.status;
}

function StageRow({
    stage,
    now,
    busy,
    onAction,
}: {
    stage: IngestStage;
    now: number;
    busy: boolean;
    onAction: (action: "retry" | "reprocess", stage: IngestStage) => void;
}) {
    const progress = progressText(stage);
    const canRetry = stage.status === "failed" || stage.status === "blocked";
    // `source` runs in the upload request; running stages can't be reset.
    const canReprocess =
        stage.name !== "source" && ["succeeded", "skipped", "failed"].includes(stage.status);
    // `error_message` is the skip reason on skipped rows.
    const note = stage.error_message;
    return (
        <li className="flex items-start gap-2 py-1.5">
            <span className="mt-0.5">
                <StatusIcon stage={stage} />
            </span>
            <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2">
                    <span className="text-sm font-medium">{stage.label}</span>
                    <span className="text-xs text-muted-foreground">{statusText(stage, now)}</span>
                    {progress && <span className="ml-auto text-xs tabular-nums text-muted-foreground">{progress}</span>}
                </div>
                {stage.model_used && (
                    <div className="truncate text-[11px] text-muted-foreground" title={stage.model_used}>
                        {stage.model_used}
                    </div>
                )}
                {note && (
                    <div
                        className={cn(
                            "break-words text-[11px]",
                            stage.status === "skipped" ? "text-muted-foreground" : "text-destructive",
                        )}
                    >
                        {note}
                    </div>
                )}
            </div>
            {canRetry && (
                <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-xs"
                    disabled={busy}
                    onClick={() => onAction("retry", stage)}
                >
                    Retry
                </Button>
            )}
            {canReprocess && (
                <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 w-6 p-0 text-muted-foreground"
                    disabled={busy}
                    title={`Reprocess ${stage.label} and everything after it`}
                    aria-label={`Reprocess ${stage.label}`}
                    onClick={() => onAction("reprocess", stage)}
                >
                    <RotateCw className="size-3.5" />
                </Button>
            )}
        </li>
    );
}

/** Header button + popover listing the paper's ingest stages. */
export function IngestStatusPopover() {
    const paperId = usePaperAtomValue(parentPaperIdAtom) || null;
    const { status, retry, reprocess } = useIngest(paperId);
    const [busy, setBusy] = useState(false);
    const [open, setOpen] = useState(false);
    const now = useNow(open && !!status?.stages.some((s) => s.status === "queued" && s.next_attempt_at));

    // Legacy papers have no stages to show.
    if (!paperId || !status || status.legacy) return null;

    const failed = status.stages.some((s) => s.status === "failed");
    const onAction = async (action: "retry" | "reprocess", stage: IngestStage) => {
        setBusy(true);
        try {
            await (action === "retry" ? retry(stage.name) : reprocess(stage.name));
        } catch (error) {
            toast.error(error instanceof Error ? error.message : `Couldn't ${action} ${stage.label}`);
        } finally {
            setBusy(false);
        }
    };

    let trigger = <CheckCircle2 className="size-3.5 text-muted-foreground" />;
    if (failed) trigger = <AlertTriangle className="size-3.5 text-destructive" />;
    else if (status.active && !status.worker_online) trigger = <WifiOff className="size-3.5 text-muted-foreground" />;
    else if (status.active) trigger = <Loader2 className="size-3.5 animate-spin text-blue-500" />;

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <Button size="sm" variant="ghost" className="h-7 gap-1.5 px-2 text-xs" aria-label="Processing status">
                    {trigger}
                    <span className="hidden sm:inline">
                        {failed ? "Processing failed" : status.active ? "Processing" : "Processed"}
                    </span>
                </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-96 max-h-[70vh] overflow-y-auto">
                <div className="mb-1 flex items-center justify-between">
                    <h3 className="text-sm font-semibold">Processing</h3>
                    {!status.worker_online && (
                        <span className="flex items-center gap-1 text-xs text-muted-foreground">
                            <WifiOff className="size-3.5" />
                            Ingest worker offline
                        </span>
                    )}
                </div>
                <ul className="divide-y">
                    {status.stages.map((stage) => (
                        <StageRow key={stage.name} stage={stage} now={now} busy={busy} onAction={onAction} />
                    ))}
                </ul>
            </PopoverContent>
        </Popover>
    );
}
