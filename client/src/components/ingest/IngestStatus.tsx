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
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
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

function useIngestPanel() {
    const paperId = usePaperAtomValue(parentPaperIdAtom) || null;
    const ingest = useIngest(paperId);
    // Legacy papers have no stages to show.
    const status = paperId && ingest.status && !ingest.status.legacy ? ingest.status : null;
    const failed = !!status?.stages.some((s) => s.status === "failed");
    return { ...ingest, status, failed };
}

/** The paper's ingest stages with retry / reprocess, as a dialog. */
export function IngestStatusDialog({
    open,
    onOpenChange,
}: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
}) {
    const { status, retry, reprocess } = useIngestPanel();
    const [busy, setBusy] = useState(false);
    const now = useNow(open && !!status?.stages.some((s) => s.status === "queued" && s.next_attempt_at));
    if (!status) return null;

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

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
                <DialogHeader>
                    <DialogTitle>Processing</DialogTitle>
                    <DialogDescription className="flex items-center gap-1.5">
                        {!status.worker_online ? (
                            <>
                                <WifiOff className="size-3.5" /> Ingest worker offline
                            </>
                        ) : status.active ? (
                            "Stages still running update live."
                        ) : (
                            "Retry a failed stage, or reprocess one and everything after it."
                        )}
                    </DialogDescription>
                </DialogHeader>
                <ul className="divide-y">
                    {status.stages.map((stage) => (
                        <StageRow key={stage.name} stage={stage} now={now} busy={busy} onAction={onAction} />
                    ))}
                </ul>
            </DialogContent>
        </Dialog>
    );
}

/** Header button: only when a stage failed. The rest of the time the stages
 * are one click away in the paper info box (`IngestStatusInfoButton`). */
export function IngestFailureButton() {
    const { failed } = useIngestPanel();
    const [open, setOpen] = useState(false);
    if (!failed) return null;
    return (
        <>
            <Button
                size="sm"
                variant="ghost"
                className="h-7 gap-1.5 px-2 text-xs"
                aria-label="Processing status"
                onClick={() => setOpen(true)}
            >
                <AlertTriangle className="size-3.5 text-destructive" />
                <span className="hidden sm:inline">Processing failed</span>
            </Button>
            <IngestStatusDialog open={open} onOpenChange={setOpen} />
        </>
    );
}

/** A row for the paper info box: current processing state; opens the dialog. */
export function IngestStatusInfoButton({ onOpen }: { onOpen: () => void }) {
    const { status, failed } = useIngestPanel();
    if (!status) return null;

    let icon = <CheckCircle2 className="size-3.5 text-green-600" />;
    let label = "Processed";
    if (failed) {
        icon = <AlertTriangle className="size-3.5 text-destructive" />;
        label = "Processing failed";
    } else if (status.active && !status.worker_online) {
        icon = <WifiOff className="size-3.5 text-muted-foreground" />;
        label = "Waiting for the ingest worker";
    } else if (status.active) {
        icon = <Loader2 className="size-3.5 animate-spin text-blue-500" />;
        label = "Processing…";
    }
    return (
        <Button variant="outline" size="sm" className="h-7 w-full justify-start gap-1.5 text-xs" onClick={onOpen}>
            {icon}
            {label}
            <span className="ml-auto text-muted-foreground">Details</span>
        </Button>
    );
}
