"use client";

import { useState, type ReactNode } from "react";
import { AlertTriangle, Loader2, RotateCw, WifiOff } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useFeatureGate, type FeatureGateState, type IngestFeature } from "@/hooks/useIngest";

export const FEATURE_LABELS: Record<IngestFeature, string> = {
    reading: "Reading",
    manual_highlights: "Highlights",
    notes: "Notes",
    metadata: "Title and authors",
    thumbnail: "Thumbnail",
    chat: "Chat",
    figures: "Figures",
    citation_jump: "Citation jump",
    outline: "Outline",
    ai_highlights: "AI highlights",
};

interface FeatureGateProps {
    paperId: string | null | undefined;
    feature: IngestFeature;
    /** Rendered once the feature is enabled. Omit to render just the notice. */
    children?: ReactNode;
    /**
     * `panel`: a centred placeholder filling the space the feature would take.
     * `inline`: a one-line notice; hidden for features that don't apply to
     * this document (e.g. AI highlights on a supplementary).
     */
    variant?: "panel" | "inline";
    className?: string;
}

/**
 * Renders `children` when the paper's ingest has made `feature` usable;
 * otherwise says what it's waiting for ("Needs OCR — retrying in 18 s"),
 * with a Retry button when a stage failed.
 */
export function FeatureGate({ paperId, feature, children, variant = "panel", className }: FeatureGateProps) {
    const gate = useFeatureGate(paperId, feature);
    if (gate.enabled) return <>{children}</>;
    if (!gate.ready) {
        return variant === "panel" ? (
            <div className={cn("flex h-full w-full items-center justify-center", className)}>
                <Loader2 className="size-4 animate-spin text-muted-foreground" />
            </div>
        ) : null;
    }
    if (variant === "inline") {
        if (!gate.applicable) return null;
        return (
            <div className={cn("flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground", className)}>
                <GateIcon gate={gate} className="size-3.5 shrink-0" />
                <span className="min-w-0 flex-1 break-words">
                    <span className="font-medium">{FEATURE_LABELS[feature]}:</span> {gate.message}
                </span>
                <RetryButton gate={gate} size="xs" />
            </div>
        );
    }
    return (
        <div
            className={cn(
                "flex h-full w-full flex-col items-center justify-center gap-3 p-6 text-center text-sm text-muted-foreground",
                className,
            )}
            role="status"
        >
            <GateIcon gate={gate} className="size-6" />
            <p className="font-medium text-foreground">{FEATURE_LABELS[feature]}</p>
            <p className="max-w-sm break-words">{gate.message}</p>
            <RetryButton gate={gate} size="sm" />
        </div>
    );
}

export function GateIcon({ gate, className }: { gate: FeatureGateState; className?: string }) {
    if (gate.retryStage) return <AlertTriangle className={cn("text-destructive", className)} />;
    if (gate.workerOffline) return <WifiOff className={className} />;
    if (gate.inProgress) return <Loader2 className={cn("animate-spin", className)} />;
    return null;
}

function RetryButton({ gate, size }: { gate: FeatureGateState; size: "xs" | "sm" }) {
    const [pending, setPending] = useState(false);
    if (!gate.retryStage) return null;
    const onClick = async () => {
        setPending(true);
        try {
            await gate.retry();
        } catch (error) {
            toast.error(error instanceof Error ? error.message : "Retry failed");
        } finally {
            setPending(false);
        }
    };
    return (
        <Button
            variant="outline"
            size="sm"
            className={size === "xs" ? "h-6 px-2 text-xs" : undefined}
            disabled={pending}
            onClick={onClick}
        >
            <RotateCw className={cn("size-3.5", pending && "animate-spin")} />
            Retry
        </Button>
    );
}
