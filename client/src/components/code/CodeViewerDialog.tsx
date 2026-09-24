"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getRepoTree, isRepoApiStatus, type RepoTree } from "@/lib/repoApi";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Loader } from "@/components/ai-elements/loader";
import { CodeDisplaySettings } from "@/components/code/CodeDisplaySettings";
import { CodeFileView, type LineRange } from "@/components/code/CodeFileView";
import { RepoFileTree } from "@/components/code/RepoFileTree";
import type { CodeViewerRequest } from "@/components/code/CodeViewerProvider";
import type { CodeQuestionModel } from "@/lib/quickQuestionApi";

/**
 * Wide two-pane browser over the repo snapshot the agent read: manifest tree
 * on the left, the selected file on the right. Opened either bare ("browse
 * code") or preselected at a citation's file + line range.
 */

interface CodeViewerDialogProps {
    paperId: string;
    open: boolean;
    onOpenChange: (open: boolean) => void;
    request: CodeViewerRequest | null;
    /** Model the chat panel has selected, forwarded to quick questions. */
    chatModel?: CodeQuestionModel | null;
}

function rangeFromRequest(request: CodeViewerRequest | null): LineRange | null {
    if (!request?.startLine) return null;
    const start = Math.max(1, Math.floor(request.startLine));
    const rawEnd = request.endLine ?? request.startLine;
    return { start, end: Math.max(start, Math.floor(rawEnd)) };
}

export function CodeViewerDialog({
    paperId,
    open,
    onOpenChange,
    request,
    chatModel,
}: CodeViewerDialogProps) {
    const [tree, setTree] = useState<RepoTree | null>(null);
    const [treeLoading, setTreeLoading] = useState(false);
    const [treeError, setTreeError] = useState<string | null>(null);
    const [selectedPath, setSelectedPath] = useState<string | null>(null);
    const [range, setRange] = useState<LineRange | null>(null);
    const [scrollToken, setScrollToken] = useState(0);
    // Revision the open request's line numbers refer to (null when opened
    // bare or from the tree).
    const [citedSha, setCitedSha] = useState<string | null>(null);

    // A different paper is a different snapshot — never show the previous
    // one's files while the new manifest loads.
    useEffect(() => {
        setTree(null);
        setTreeError(null);
        setSelectedPath(null);
        setRange(null);
        setCitedSha(null);
    }, [paperId]);

    // Refetched on every open: disconnect/reconnect or a re-ingest replaces
    // the snapshot, and a cached manifest would show files that no longer
    // exist. The previous tree stays on screen until the new one lands.
    useEffect(() => {
        if (!open || !paperId) return;
        let cancelled = false;
        setTreeLoading(true);
        setTreeError(null);
        getRepoTree(paperId)
            .then((result) => {
                if (cancelled) return;
                setTree(result);
                // Drop a selection that the new snapshot no longer contains.
                setSelectedPath((current) =>
                    current && !result.files.some((f) => f.path === current)
                        ? null
                        : current
                );
            })
            .catch((error: unknown) => {
                if (cancelled) return;
                if (isRepoApiStatus(error, 404)) {
                    setTreeError(
                        "No repo snapshot is connected to this paper yet."
                    );
                    return;
                }
                const message =
                    error instanceof Error ? error.message : "Unknown error";
                setTreeError(`Could not load the repo file list: ${message}`);
            })
            .finally(() => {
                if (!cancelled) setTreeLoading(false);
            });
        return () => {
            cancelled = true;
        };
    }, [open, paperId]);

    // Apply each open request: preselect its file / range, or leave the
    // browser as-is when opened bare.
    useEffect(() => {
        if (!request) return;
        if (request.path) {
            setSelectedPath(request.path.replace(/^\/+/, ""));
            setRange(rangeFromRequest(request));
            setCitedSha(request.commitSha ?? null);
        }
        setScrollToken(request.requestId);
    }, [request]);

    // Radix dismisses the dialog from a capture-phase Escape listener on
    // `document`, which no keydown handler inside the content can beat. The
    // quick-question panel registers here instead, and Escape closes it first.
    const escapeHandlerRef = useRef<(() => boolean) | null>(null);
    const registerEscapeHandler = useCallback(
        (handler: (() => boolean) | null) => {
            escapeHandlerRef.current = handler;
        },
        []
    );

    const handleSelect = (path: string) => {
        setSelectedPath(path);
        // A manually opened file has no cited range to emphasize.
        setRange(null);
        setCitedSha(null);
    };

    // A citation pins the revision it was verified against. After a
    // disconnect + reconnect the snapshot can be a different commit, and the
    // cited line numbers then point at unrelated code — say so.
    const revisionNotice =
        selectedPath &&
        citedSha &&
        tree?.commit_sha &&
        citedSha !== tree.commit_sha
            ? `This citation was verified against revision ${citedSha.slice(0, 7)}; the connected snapshot is now ${tree.commit_sha.slice(0, 7)}, so the highlighted lines may not match.`
            : null;

    const repoLabel = tree?.owner && tree?.repo ? `${tree.owner}/${tree.repo}` : "Repository";
    const shortSha = tree?.commit_sha ? tree.commit_sha.slice(0, 7) : null;

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent
                className="flex h-[85vh] w-[95vw] max-w-6xl flex-col gap-0 overflow-hidden p-0 sm:max-w-6xl"
                onEscapeKeyDown={(event) => {
                    if (escapeHandlerRef.current?.()) event.preventDefault();
                }}
            >
                <DialogHeader className="shrink-0 border-b border-border/60 px-4 py-2.5 text-left">
                    <DialogTitle className="flex items-center gap-2 text-sm font-medium">
                        <span className="truncate">{repoLabel}</span>
                        {shortSha && (
                            <span className="shrink-0 font-mono text-[11px] font-normal text-muted-foreground">
                                @{shortSha}
                            </span>
                        )}
                        {/* Sits left of the dialog's own close button. */}
                        <span className="ml-auto pr-6">
                            <CodeDisplaySettings />
                        </span>
                    </DialogTitle>
                    <DialogDescription className="sr-only">
                        Browse the repository snapshot this paper&apos;s
                        assistant reads from.
                    </DialogDescription>
                </DialogHeader>

                <div className="flex min-h-0 flex-1">
                    <div className="w-40 shrink-0 border-r border-border/60 sm:w-56 md:w-72">
                        {treeLoading && !tree ? (
                            <div className="flex items-center gap-2 p-4 text-xs text-muted-foreground">
                                <Loader size={14} />
                                <span>Loading files…</span>
                            </div>
                        ) : treeError ? (
                            <p className="p-4 text-xs text-destructive">
                                {treeError}
                            </p>
                        ) : tree && tree.files.length > 0 ? (
                            <RepoFileTree
                                files={tree.files}
                                selectedPath={selectedPath}
                                onSelect={handleSelect}
                            />
                        ) : (
                            <p className="p-4 text-xs text-muted-foreground">
                                This snapshot has no files.
                            </p>
                        )}
                    </div>

                    <div className="flex min-w-0 flex-1 flex-col">
                        {revisionNotice && (
                            <p
                                role="status"
                                className="shrink-0 border-b border-amber-300/60 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
                            >
                                {revisionNotice}
                            </p>
                        )}
                        <div className="min-h-0 flex-1">
                        {selectedPath ? (
                            <CodeFileView
                                paperId={paperId}
                                path={selectedPath}
                                range={range}
                                scrollToken={scrollToken}
                                viewerOpen={open}
                                chatModel={chatModel}
                                registerEscapeHandler={registerEscapeHandler}
                            />
                        ) : (
                            <div className="flex h-full items-center justify-center p-6 text-xs text-muted-foreground">
                                Select a file to view its contents.
                            </div>
                        )}
                        </div>
                    </div>
                </div>
            </DialogContent>
        </Dialog>
    );
}
