"use client";

import { FileCodeIcon } from "lucide-react";

import { useCodeViewer } from "@/components/code/CodeViewerProvider";

/**
 * Files a `run_python` sandbox call touched, as chips on the tool row. Each
 * one opens the code viewer at that file.
 */

interface RepoFileChipsProps {
    files: string[];
}

export function RepoFileChips({ files }: RepoFileChipsProps) {
    const { openCodeViewer } = useCodeViewer();

    if (files.length === 0) return null;

    return (
        <div className="ml-4 flex max-w-full min-w-0 flex-wrap gap-1 py-0.5">
            {files.map((file) => (
                <button
                    key={file}
                    type="button"
                    onClick={() => openCodeViewer({ path: file })}
                    title={`Open ${file}`}
                    className="flex max-w-[220px] min-w-0 items-center gap-1 rounded border border-border/60 bg-muted/40 px-1.5 py-0.5 text-[10px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                    <FileCodeIcon className="size-2.5 shrink-0" />
                    <span className="truncate font-mono">
                        {file.split("/").pop() || file}
                    </span>
                </button>
            ))}
        </div>
    );
}
