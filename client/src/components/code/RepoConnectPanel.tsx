"use client";

import { useEffect, useState, type FormEvent } from "react";
import {
    AlertTriangleIcon,
    ExternalLinkIcon,
    FolderOpenIcon,
    RefreshCwIcon,
    UnlinkIcon,
} from "lucide-react";
import { toast } from "sonner";

import { formatBytes, looksLikeGithubRepoUrl } from "@/lib/repoApi";
import { useRepoStatus } from "@/hooks/useRepoStatus";
import { useCodeViewer } from "@/components/code/CodeViewerProvider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loader } from "@/components/ai-elements/loader";

/**
 * Repo connection for a paper: paste a GitHub URL to give the assistant read
 * access to the paper's code, watch ingestion, then browse the snapshot or
 * disconnect it. Rendered inline in the paper info panel.
 */

interface RepoConnectPanelProps {
    paperId: string;
    /** Runs before the code viewer opens (e.g. to close the surrounding menu). */
    onBrowse?: () => void;
}

export function RepoConnectPanel({ paperId, onBrowse }: RepoConnectPanelProps) {
    const { repo, loading, error, mutating, refresh, connect, disconnect } =
        useRepoStatus(paperId);
    const { openCodeViewer } = useCodeViewer();

    const [url, setUrl] = useState("");
    const [formError, setFormError] = useState<string | null>(null);
    const [confirmingDisconnect, setConfirmingDisconnect] = useState(false);

    const status = repo?.status ?? null;
    const busy = status === "pending" || status === "ingesting";

    useEffect(() => {
        if (!repo) setConfirmingDisconnect(false);
    }, [repo]);

    const handleConnect = async (event: FormEvent) => {
        event.preventDefault();
        const trimmed = url.trim();
        if (!looksLikeGithubRepoUrl(trimmed)) {
            setFormError("Enter a GitHub repo URL, e.g. https://github.com/owner/repo");
            return;
        }
        setFormError(null);
        try {
            await connect(trimmed);
            setUrl("");
        } catch (err) {
            setFormError(err instanceof Error ? err.message : "Could not connect that repo.");
        }
    };

    const handleRetry = async () => {
        if (!repo) return;
        setFormError(null);
        try {
            await connect(`https://github.com/${repo.owner}/${repo.repo}`);
        } catch (err) {
            setFormError(err instanceof Error ? err.message : "Could not retry ingestion.");
        }
    };

    const handleDisconnect = async () => {
        try {
            await disconnect();
            setConfirmingDisconnect(false);
            toast.success("Repository disconnected.");
        } catch (err) {
            const message =
                err instanceof Error ? err.message : "Could not disconnect the repo.";
            toast.error(message);
            // A 409 means ingestion is still running — resync so the UI agrees.
            refresh();
        }
    };

    return (
        <div className="space-y-3">
            {loading && !repo ? (
                <p className="text-xs text-muted-foreground">Checking…</p>
            ) : error && !repo ? (
                <div className="space-y-2">
                    <p className="text-xs text-destructive">{error}</p>
                    <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="h-7 text-xs"
                        onClick={() => refresh()}
                    >
                        <RefreshCwIcon className="size-3" />
                        Try again
                    </Button>
                </div>
            ) : !repo ? (
                <form onSubmit={handleConnect} className="space-y-2">
                    <p className="text-xs text-muted-foreground">
                        Connect the paper&apos;s GitHub repo so the
                        assistant can read and cite its code.
                    </p>
                    <Input
                        value={url}
                        onChange={(event) => {
                            setUrl(event.currentTarget.value);
                            setFormError(null);
                        }}
                        placeholder="https://github.com/owner/repo"
                        aria-label="GitHub repository URL"
                        className="h-8 text-xs"
                        disabled={mutating}
                    />
                    {formError && (
                        <p className="text-xs text-destructive">{formError}</p>
                    )}
                    <Button
                        type="submit"
                        size="sm"
                        className="h-7 w-full text-xs"
                        disabled={mutating || !url.trim()}
                    >
                        {mutating ? "Connecting…" : "Connect"}
                    </Button>
                </form>
            ) : (
                <div className="space-y-3">
                    <div className="space-y-1">
                        <a
                            href={`https://github.com/${repo.owner}/${repo.repo}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex items-center gap-1 text-xs font-medium text-blue-600 hover:underline dark:text-blue-400"
                        >
                            <span className="truncate">
                                {repo.owner}/{repo.repo}
                            </span>
                            <ExternalLinkIcon className="size-3 shrink-0" />
                        </a>
                        <p className="text-[11px] text-muted-foreground">
                            {repo.ref}
                            {repo.commit_sha
                                ? ` · ${repo.commit_sha.slice(0, 7)}`
                                : ""}
                        </p>
                    </div>

                    {busy && (
                        <p className="flex gap-1.5 text-xs text-muted-foreground">
                            <Loader size={12} className="mt-0.5 shrink-0" />
                            <span>
                            {status === "pending"
                                ? "Queued for ingestion…"
                                : "Downloading and indexing the repo…"}{" "}
                            This usually takes under a minute.
                            </span>
                        </p>
                    )}

                    {status === "ready" && (
                        <>
                            <p className="text-xs text-muted-foreground">
                                {repo.file_count ?? 0} files
                                {repo.total_bytes
                                    ? ` · ${formatBytes(repo.total_bytes)}`
                                    : ""}{" "}
                                available to the assistant.
                            </p>
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                className="h-7 w-full text-xs"
                                onClick={() => {
                                onBrowse?.();
                                openCodeViewer();
                            }}
                            >
                                <FolderOpenIcon className="size-3" />
                                Browse code
                            </Button>
                        </>
                    )}

                    {status === "error" && (
                        <div className="space-y-2">
                            <div className="flex items-start gap-1.5 rounded-md border border-destructive/40 bg-destructive/5 p-2">
                                <AlertTriangleIcon className="mt-0.5 size-3 shrink-0 text-destructive" />
                                <p className="text-[11px] break-words text-destructive">
                                    {repo.error ?? "Ingestion failed."}
                                </p>
                            </div>
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                className="h-7 w-full text-xs"
                                onClick={handleRetry}
                                disabled={mutating}
                            >
                                <RefreshCwIcon className="size-3" />
                                {mutating ? "Retrying…" : "Retry"}
                            </Button>
                        </div>
                    )}

                    {formError && (
                        <p className="text-xs text-destructive">{formError}</p>
                    )}

                    {confirmingDisconnect ? (
                        <div className="space-y-2 rounded-md border border-border/60 p-2">
                            <p className="text-[11px] text-muted-foreground">
                                Disconnect {repo.owner}/{repo.repo}? The
                                assistant loses access to its code.
                            </p>
                            <div className="flex gap-2">
                                <Button
                                    type="button"
                                    variant="destructive"
                                    size="sm"
                                    className="h-7 flex-1 text-xs"
                                    onClick={handleDisconnect}
                                    disabled={mutating}
                                >
                                    Disconnect
                                </Button>
                                <Button
                                    type="button"
                                    variant="ghost"
                                    size="sm"
                                    className="h-7 flex-1 text-xs"
                                    onClick={() => setConfirmingDisconnect(false)}
                                >
                                    Cancel
                                </Button>
                            </div>
                        </div>
                    ) : (
                        <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="h-7 w-full text-xs text-muted-foreground hover:text-destructive"
                            onClick={() => setConfirmingDisconnect(true)}
                            disabled={mutating}
                        >
                            <UnlinkIcon className="size-3" />
                            Disconnect
                        </Button>
                    )}
                </div>
            )}
        </div>
    );
}
