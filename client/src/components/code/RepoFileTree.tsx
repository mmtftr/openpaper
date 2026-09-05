"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronRightIcon, FileIcon, FolderIcon, SearchIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { formatBytes, type RepoTreeFile } from "@/lib/repoApi";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";

/** Left pane of the code viewer: the snapshot manifest as a browsable tree. */

interface TreeFileNode {
    kind: "file";
    name: string;
    path: string;
    size: number;
}

interface TreeDirNode {
    kind: "dir";
    name: string;
    path: string;
    children: TreeNode[];
}

type TreeNode = TreeFileNode | TreeDirNode;

/** Max rows shown for a filter match — long lists stop being browsable. */
const MAX_FILTER_RESULTS = 300;

function buildTree(files: RepoTreeFile[]): TreeNode[] {
    const root: TreeDirNode = { kind: "dir", name: "", path: "", children: [] };
    const dirs = new Map<string, TreeDirNode>([["", root]]);

    const dirFor = (path: string): TreeDirNode => {
        const existing = dirs.get(path);
        if (existing) return existing;
        const slash = path.lastIndexOf("/");
        const parent = dirFor(slash === -1 ? "" : path.slice(0, slash));
        const node: TreeDirNode = {
            kind: "dir",
            name: slash === -1 ? path : path.slice(slash + 1),
            path,
            children: [],
        };
        parent.children.push(node);
        dirs.set(path, node);
        return node;
    };

    for (const file of files) {
        const path = file.path.replace(/^\/+/, "");
        if (!path) continue;
        const slash = path.lastIndexOf("/");
        const parent = dirFor(slash === -1 ? "" : path.slice(0, slash));
        parent.children.push({
            kind: "file",
            name: slash === -1 ? path : path.slice(slash + 1),
            path,
            size: file.size,
        });
    }

    const sortNodes = (nodes: TreeNode[]): TreeNode[] => {
        nodes.sort((a, b) => {
            if (a.kind !== b.kind) return a.kind === "dir" ? -1 : 1;
            return a.name.localeCompare(b.name);
        });
        for (const node of nodes) {
            if (node.kind === "dir") sortNodes(node.children);
        }
        return nodes;
    };

    return sortNodes(root.children);
}

function ancestorsOf(path: string): string[] {
    const parts = path.split("/");
    parts.pop();
    const result: string[] = [];
    let prefix = "";
    for (const part of parts) {
        prefix = prefix ? `${prefix}/${part}` : part;
        result.push(prefix);
    }
    return result;
}

interface RepoFileTreeProps {
    files: RepoTreeFile[];
    selectedPath: string | null;
    onSelect: (path: string) => void;
}

export function RepoFileTree({
    files,
    selectedPath,
    onSelect,
}: RepoFileTreeProps) {
    const [filter, setFilter] = useState("");
    const [expanded, setExpanded] = useState<Set<string>>(new Set());

    const tree = useMemo(() => buildTree(files), [files]);

    // Keep the selected file reachable: expand every directory above it.
    useEffect(() => {
        if (!selectedPath) return;
        const ancestors = ancestorsOf(selectedPath);
        if (ancestors.length === 0) return;
        setExpanded((prev) => {
            if (ancestors.every((dir) => prev.has(dir))) return prev;
            const next = new Set(prev);
            for (const dir of ancestors) next.add(dir);
            return next;
        });
    }, [selectedPath]);

    // A repo whose whole tree hangs off one directory reads better opened.
    useEffect(() => {
        if (tree.length === 1 && tree[0].kind === "dir") {
            const only = tree[0].path;
            setExpanded((prev) => (prev.has(only) ? prev : new Set(prev).add(only)));
        }
    }, [tree]);

    const matches = useMemo(() => {
        const needle = filter.trim().toLowerCase();
        if (!needle) return null;
        return files
            .filter((file) => file.path.toLowerCase().includes(needle))
            .sort((a, b) => a.path.localeCompare(b.path))
            .slice(0, MAX_FILTER_RESULTS);
    }, [files, filter]);

    const toggleDir = (path: string) => {
        setExpanded((prev) => {
            const next = new Set(prev);
            if (next.has(path)) next.delete(path);
            else next.add(path);
            return next;
        });
    };

    const renderNodes = (nodes: TreeNode[], depth: number) =>
        nodes.map((node) => {
            const indent = { paddingLeft: `${depth * 12 + 8}px` };
            if (node.kind === "dir") {
                const isOpen = expanded.has(node.path);
                return (
                    <div key={node.path}>
                        <button
                            type="button"
                            onClick={() => toggleDir(node.path)}
                            style={indent}
                            aria-expanded={isOpen}
                            className="flex w-full items-center gap-1 rounded py-0.5 pr-2 text-left text-xs text-muted-foreground hover:bg-muted/60 hover:text-foreground"
                        >
                            <ChevronRightIcon
                                className={cn(
                                    "size-3 shrink-0 transition-transform",
                                    isOpen && "rotate-90"
                                )}
                            />
                            <FolderIcon className="size-3 shrink-0" />
                            <span className="truncate">{node.name}</span>
                        </button>
                        {isOpen && renderNodes(node.children, depth + 1)}
                    </div>
                );
            }
            return (
                <FileRow
                    key={node.path}
                    path={node.path}
                    label={node.name}
                    size={node.size}
                    style={indent}
                    selected={selectedPath === node.path}
                    onSelect={onSelect}
                />
            );
        });

    return (
        <div className="flex h-full min-h-0 flex-col">
            <div className="relative p-2">
                <SearchIcon className="pointer-events-none absolute top-1/2 left-4 size-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                    value={filter}
                    onChange={(event) => setFilter(event.currentTarget.value)}
                    placeholder="Filter files…"
                    aria-label="Filter files"
                    className="h-8 pl-7 text-xs"
                />
            </div>
            <ScrollArea className="min-h-0 flex-1">
                <div className="pb-3">
                    {matches ? (
                        matches.length === 0 ? (
                            <p className="px-3 py-2 text-xs text-muted-foreground">
                                No files match “{filter.trim()}”.
                            </p>
                        ) : (
                            <>
                                {matches.map((file) => (
                                    <FileRow
                                        key={file.path}
                                        path={file.path}
                                        label={file.path}
                                        size={file.size}
                                        style={{ paddingLeft: "8px" }}
                                        selected={selectedPath === file.path}
                                        onSelect={onSelect}
                                    />
                                ))}
                                {matches.length === MAX_FILTER_RESULTS && (
                                    <p className="px-3 py-2 text-[11px] text-muted-foreground">
                                        Showing the first {MAX_FILTER_RESULTS}{" "}
                                        matches — narrow the filter to see more.
                                    </p>
                                )}
                            </>
                        )
                    ) : (
                        renderNodes(tree, 0)
                    )}
                </div>
            </ScrollArea>
        </div>
    );
}

interface FileRowProps {
    path: string;
    label: string;
    size: number;
    style: React.CSSProperties;
    selected: boolean;
    onSelect: (path: string) => void;
}

function FileRow({ path, label, size, style, selected, onSelect }: FileRowProps) {
    return (
        <button
            type="button"
            onClick={() => onSelect(path)}
            style={style}
            title={path}
            aria-current={selected ? "true" : undefined}
            className={cn(
                "flex w-full items-center gap-1 rounded py-0.5 pr-2 text-left text-xs hover:bg-muted/60",
                selected
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground hover:text-foreground"
            )}
        >
            <FileIcon className="size-3 shrink-0 opacity-70" />
            <span className="flex-1 truncate">{label}</span>
            <span className="shrink-0 text-[10px] tabular-nums opacity-60">
                {formatBytes(size)}
            </span>
        </button>
    );
}
