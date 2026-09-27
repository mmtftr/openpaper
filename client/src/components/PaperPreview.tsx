"use client";

import { api, unwrap, type Schemas } from "@/lib/api/client";
import type { LibraryPaper } from "./LibraryTable";
import { Button } from "./ui/button";
import { X, ExternalLink, Highlighter, Plus, FileText, Download, Pencil, Archive, ArchiveRestore } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";
import { handleStatusChange, truncateText } from "@/components/utils/paperUtils";
import { getStatusIcon, PaperStatusEnum } from "@/components/utils/PdfStatus";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { PaperProjects } from "./PaperProjects";
import { TagSelector } from "./TagSelector";
import { useHighlighterHighlights } from "@/hooks/PdfHighlighterHighlights";
import { useEffect, useState, useRef, useCallback } from "react";
import useSWR from "swr";
import { CitePaperButton } from "./CitePaperButton";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

function autoResize(el: HTMLTextAreaElement) {
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
}

interface PaperPreviewProps {
    paper: LibraryPaper;
    onClose: () => void;
    setPaper: (paperId: string, updatedPaper: LibraryPaper) => void;
    /** Archive (true) or unarchive (false) this paper; no button without it. */
    onArchive?: (archived: boolean) => void;
    className?: string;
}

function EditableField({
    value,
    onSave,
    multiline: _multiline = false,
    className = "",
    placeholder = "",
}: {
    value: string;
    onSave: (value: string) => void;
    multiline?: boolean;
    className?: string;
    placeholder?: string;
}) {
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState(value);
    const inputRef = useRef<HTMLTextAreaElement>(null);

    useEffect(() => {
        setDraft(value);
    }, [value]);

    useEffect(() => {
        if (editing && inputRef.current) {
            autoResize(inputRef.current);
            inputRef.current.focus();
            inputRef.current.select();
        }
    }, [editing]);

    const cancel = useCallback(() => {
        setDraft(value);
        setEditing(false);
    }, [value]);

    const save = useCallback(() => {
        const trimmed = draft.trim();
        setEditing(false);
        if (trimmed !== value) {
            onSave(trimmed);
        }
    }, [draft, value, onSave]);

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === "Enter") {
            e.preventDefault();
            save();
        }
        if (e.key === "Escape") {
            cancel();
        }
    };

    if (editing) {
        return (
            <div>
                <Textarea
                    ref={inputRef}
                    value={draft}
                    onChange={(e) => {
                        setDraft(e.target.value);
                        autoResize(e.target);
                    }}
                    onBlur={cancel}
                    onKeyDown={handleKeyDown}
                    className={`text-sm resize-none overflow-hidden ${className}`}
                    placeholder={placeholder}
                    rows={1}
                />
                <span className="text-xs text-muted-foreground mt-1 block">
                    ↵ Enter to save · Esc to cancel
                </span>
            </div>
        );
    }

    return (
        <span
            className={`group/edit cursor-pointer hover:bg-muted/50 rounded px-1 -mx-1 inline-flex items-center gap-1 ${className}`}
            onClick={() => setEditing(true)}
        >
            <span>{value || <span className="text-muted-foreground italic">{placeholder || "Click to add"}</span>}</span>
            <Pencil className="h-3 w-3 text-muted-foreground opacity-0 group-hover/edit:opacity-100 transition-opacity flex-shrink-0" />
        </span>
    );
}

function EditableListField({
    values,
    onSave,
    placeholder = "",
}: {
    values: string[];
    onSave: (values: string[]) => void;
    placeholder?: string;
}) {
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState(values.join(", "));
    const inputRef = useRef<HTMLTextAreaElement>(null);

    useEffect(() => {
        setDraft(values.join(", "));
    }, [values]);

    useEffect(() => {
        if (editing && inputRef.current) {
            autoResize(inputRef.current);
            inputRef.current.focus();
        }
    }, [editing]);

    const cancel = useCallback(() => {
        setDraft(values.join(", "));
        setEditing(false);
    }, [values]);

    const save = useCallback(() => {
        setEditing(false);
        const newValues = draft
            .split(",")
            .map((v) => v.trim())
            .filter(Boolean);
        if (JSON.stringify(newValues) !== JSON.stringify(values)) {
            onSave(newValues);
        }
    }, [draft, values, onSave]);

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === "Enter") {
            e.preventDefault();
            save();
        }
        if (e.key === "Escape") {
            cancel();
        }
    };

    if (editing) {
        return (
            <div>
                <Textarea
                    ref={inputRef}
                    value={draft}
                    onChange={(e) => {
                        setDraft(e.target.value);
                        autoResize(e.target);
                    }}
                    onBlur={cancel}
                    onKeyDown={handleKeyDown}
                    className="text-sm resize-none overflow-hidden"
                    placeholder={placeholder || "Comma-separated values"}
                    rows={1}
                />
                <span className="text-xs text-muted-foreground mt-1 block">
                    ↵ Enter to save · Esc to cancel
                </span>
            </div>
        );
    }

    return (
        <span
            className="group/edit cursor-pointer hover:bg-muted/50 rounded px-1 -mx-1 inline-flex items-center gap-1"
            onClick={() => setEditing(true)}
        >
            <span>{values.length > 0 ? values.join(", ") : <span className="text-muted-foreground italic">{placeholder || "Click to add"}</span>}</span>
            <Pencil className="h-3 w-3 text-muted-foreground opacity-0 group-hover/edit:opacity-100 transition-opacity flex-shrink-0" />
        </span>
    );
}

export function PaperPreview({ paper, onClose, setPaper, onArchive, className }: PaperPreviewProps) {
    const { highlights } = useHighlighterHighlights(paper.id);
    const [showAllHighlights, setShowAllHighlights] = useState(false);
    const [previewLoaded, setPreviewLoaded] = useState(false);

    // The full paper record (DOI, journal, publisher, file URL, ...).
    const { data: loadedPaper, mutate: mutateLoadedPaper } = useSWR(
        ["/api/paper", paper.id],
        () => unwrap(api.GET("/api/paper", { params: { query: { id: paper.id } } })),
        { onError: (error) => console.error("Failed to load paper data", error) },
    );

    useEffect(() => {
        setPreviewLoaded(false);
    }, [paper.id]);

    const highlightCount = highlights?.filter(highlight => highlight.role === 'user').length || 0;

    const updateField = async (fields: Schemas["UpdatePaperFieldsRequest"]) => {
        try {
            await unwrap(api.PATCH("/api/paper", {
                params: { query: { paper_id: paper.id } },
                body: fields,
            }));
            const updatedPaper = { ...paper, ...fields };
            setPaper(paper.id, updatedPaper);
            mutateLoadedPaper(
                (current) => current && { ...current, ...fields },
                { revalidate: false },
            );
        } catch (error) {
            console.error("Failed to update paper", error);
            toast.error("Failed to update paper.");
        }
    };

    const handleRemoveTag = async (tagId: string) => {
        try {
            await unwrap(api.DELETE("/api/paper/tag/papers/{paper_id}/tags/{tag_id}", {
                params: { path: { paper_id: paper.id, tag_id: tagId } },
            }));
            const updatedPaper = {
                ...paper,
                tags: paper.tags?.filter(t => t.id !== tagId)
            };
            setPaper(paper.id, updatedPaper);
        } catch (error) {
            console.error("Failed to remove tag", error);
            toast.error("Failed to remove tag.");
        }
    };

    const onTagsApplied = () => {
        // Let's try to update the paper by refetching it.
        unwrap(api.GET("/api/paper", { params: { query: { id: paper.id } } })).then(updatedPaper => {
            setPaper(paper.id, updatedPaper);
        });
    };

    return (
        <div className={cn("flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-xl border bg-card", className)}>
            <div className="flex-grow p-4 relative overflow-y-auto">
                <Button
                    variant="ghost"
                    size="icon"
                    className="absolute top-2 right-2 z-10 text-muted-foreground"
                    onClick={onClose}
                    aria-label="Close preview"
                >
                    <X className="h-4 w-4" />
                </Button>

                {/* Title + Actions */}
                <div>
                    <h3 className="mb-3 pr-10 text-lg leading-snug font-semibold">
                        <Link
                            href={`/paper/${paper.id}`}
                            className="group/title rounded-sm transition-colors duration-150 hover:text-brand focus-visible:ring-2 focus-visible:ring-brand/40 focus-visible:outline-none"
                        >
                            {paper.title || 'Untitled'}
                            <ExternalLink className="ml-1.5 inline-block size-3.5 -translate-y-px text-muted-foreground transition-colors group-hover/title:text-brand" />
                        </Link>
                    </h3>
                    <div className="flex items-center gap-2 flex-wrap">
                        <Button asChild size="sm" className="h-8 bg-brand px-3 text-xs text-brand-foreground hover:bg-brand/90">
                            <Link href={`/paper/${paper.id}`}>
                                <FileText className="h-3.5 w-3.5 mr-1.5" />
                                Open
                            </Link>
                        </Button>
                        {loadedPaper?.file_url && (
                            <Button asChild size="sm" variant="outline" className="h-8 px-3 text-xs">
                                <a href={loadedPaper.file_url} target="_blank" rel="noopener noreferrer">
                                    <Download className="h-3.5 w-3.5 mr-1.5" />
                                    Download
                                </a>
                            </Button>
                        )}
                        <CitePaperButton paper={[loadedPaper ?? paper]} minimalist={true} variant="outline" />
                        {paper.status && (
                            <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                    <Button size="sm" variant="outline" className="h-8 px-3 text-xs capitalize">
                                        <span className="flex items-center gap-2">
                                            {getStatusIcon(paper.status)}
                                            {paper.status}
                                        </span>
                                    </Button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="end">
                                    <DropdownMenuItem onClick={() => handleStatusChange(paper, PaperStatusEnum.TODO, setPaper)}>
                                        {getStatusIcon(PaperStatusEnum.TODO)}
                                        Todo
                                    </DropdownMenuItem>
                                    <DropdownMenuItem onClick={() => handleStatusChange(paper, PaperStatusEnum.READING, setPaper)}>
                                        {getStatusIcon(PaperStatusEnum.READING)}
                                        Reading
                                    </DropdownMenuItem>
                                    <DropdownMenuItem onClick={() => handleStatusChange(paper, PaperStatusEnum.COMPLETED, setPaper)}>
                                        {getStatusIcon(PaperStatusEnum.COMPLETED)}
                                        Completed
                                    </DropdownMenuItem>
                                </DropdownMenuContent>
                            </DropdownMenu>
                        )}
                        {onArchive && (
                            <Button
                                size="sm"
                                variant="outline"
                                className="h-8 px-3 text-xs"
                                onClick={() => onArchive(!paper.archived_at)}
                            >
                                {paper.archived_at ? (
                                    <ArchiveRestore className="h-3.5 w-3.5 mr-1.5" />
                                ) : (
                                    <Archive className="h-3.5 w-3.5 mr-1.5" />
                                )}
                                {paper.archived_at ? "Unarchive" : "Archive"}
                            </Button>
                        )}
                    </div>
                </div>

                {/* Preview Image */}
                {paper.preview_url && (
                    <div className="border-t border-border pt-4 mt-4">
                        {!previewLoaded && (
                            <Skeleton className="w-full aspect-[3/4] rounded-md" />
                        )}
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                            key={paper.id}
                            src={paper.preview_url}
                            alt="Paper preview"
                            className={`w-full h-auto rounded-md border animate-in fade-in duration-200 ${!previewLoaded ? "hidden" : ""}`}
                            onLoad={() => setPreviewLoaded(true)}
                        />
                    </div>
                )}

                {/* Paper Info Section - Tabular Layout */}
                <div className="border-t border-border pt-4 mt-4">
                    <div className="text-sm border rounded-md overflow-hidden">
                        <table className="w-full">
                            <tbody className="divide-y divide-border">
                                <tr>
                                    <td className="px-3 py-2 text-muted-foreground font-medium text-right whitespace-nowrap align-top w-24">Title</td>
                                    <td className="px-3 py-2">
                                        <EditableField
                                            value={paper.title || ""}
                                            onSave={(value) => updateField({ title: value })}
                                            placeholder="Add title"
                                        />
                                    </td>
                                </tr>
                                <tr>
                                    <td className="px-3 py-2 text-muted-foreground font-medium text-right whitespace-nowrap align-top w-24">Authors</td>
                                    <td className="px-3 py-2">
                                        <EditableListField
                                            values={paper.authors || []}
                                            onSave={(values) => updateField({ authors: values })}
                                            placeholder="Add authors"
                                        />
                                    </td>
                                </tr>
                                <tr>
                                    <td className="px-3 py-2 text-muted-foreground font-medium text-right whitespace-nowrap align-top w-24">Published</td>
                                    <td className="px-3 py-2">
                                        <EditableField
                                            value={paper.publish_date ? new Date(paper.publish_date).toLocaleDateString() : ""}
                                            onSave={(value) => {
                                                const parsed = new Date(value);
                                                if (!isNaN(parsed.getTime())) {
                                                    updateField({ publish_date: parsed.toISOString() });
                                                } else {
                                                    toast.error("Invalid date format");
                                                }
                                            }}
                                            placeholder="Add publish date"
                                        />
                                    </td>
                                </tr>
                                {!loadedPaper && (
                                    <>
                                        <tr>
                                            <td className="px-3 py-2 text-right w-24"><Skeleton className="h-4 w-12 ml-auto" /></td>
                                            <td className="px-3 py-2"><Skeleton className="h-4 w-48" /></td>
                                        </tr>
                                        <tr>
                                            <td className="px-3 py-2 text-right w-24"><Skeleton className="h-4 w-16 ml-auto" /></td>
                                            <td className="px-3 py-2"><Skeleton className="h-4 w-32" /></td>
                                        </tr>
                                    </>
                                )}
                                {loadedPaper && (
                                    <tr>
                                        <td className="px-3 py-2 text-muted-foreground font-medium text-right whitespace-nowrap align-top w-24">DOI</td>
                                        <td className="px-3 py-2">
                                            <EditableField
                                                value={loadedPaper.doi || ""}
                                                onSave={(value) => updateField({ doi: value })}
                                                placeholder="Add DOI"
                                            />
                                        </td>
                                    </tr>
                                )}
                                {loadedPaper && (
                                    <tr>
                                        <td className="px-3 py-2 text-muted-foreground font-medium text-right whitespace-nowrap align-top w-24">Journal</td>
                                        <td className="px-3 py-2">
                                            <EditableField
                                                value={loadedPaper.journal || ""}
                                                onSave={(value) => updateField({ journal: value })}
                                                placeholder="Add journal"
                                            />
                                        </td>
                                    </tr>
                                )}
                                {loadedPaper && (
                                    <tr>
                                        <td className="px-3 py-2 text-muted-foreground font-medium text-right whitespace-nowrap align-top w-24">Publisher</td>
                                        <td className="px-3 py-2">
                                            <EditableField
                                                value={loadedPaper.publisher || ""}
                                                onSave={(value) => updateField({ publisher: value })}
                                                placeholder="Add publisher"
                                            />
                                        </td>
                                    </tr>
                                )}
                                <tr>
                                    <td className="px-3 py-2 text-muted-foreground font-medium text-right whitespace-nowrap align-top w-24">Institutions</td>
                                    <td className="px-3 py-2">
                                        <EditableListField
                                            values={paper.institutions || []}
                                            onSave={(values) => updateField({ institutions: values })}
                                            placeholder="Add institutions"
                                        />
                                    </td>
                                </tr>
                                <tr>
                                    <td className="px-3 py-2 text-muted-foreground font-medium text-right whitespace-nowrap align-top w-24">Keywords</td>
                                    <td className="px-3 py-2">
                                        <EditableListField
                                            values={paper.keywords || []}
                                            onSave={(values) => updateField({ keywords: values })}
                                            placeholder="Add keywords"
                                        />
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                {/* Highlights Section */}
                {highlightCount > 0 && (
                    <div className="border-t border-border pt-4 mt-4 space-y-2">
                        <h4 className="font-semibold text-sm flex items-center gap-2">
                            <Highlighter className="h-4 w-4 text-yellow-600" />
                            Highlights ({highlightCount})
                        </h4>
                        <div className="space-y-3">
                            {highlights.filter(highlight => highlight.role === 'user').slice(0, showAllHighlights ? undefined : 3).map((highlight) => (
                                <div key={highlight.id} className="p-2 border-l-2 border-yellow-400 bg-yellow-50/50 dark:bg-yellow-950/20 rounded-r">
                                    <p className="text-sm">
                                        {truncateText(highlight.raw_text, 200)}
                                    </p>
                                    {highlight.page_number != null && (
                                        <div className="flex items-center gap-2 mt-1 text-xs text-muted-foreground">
                                            <span>Page {highlight.page_number + 1}</span>
                                        </div>
                                    )}
                                </div>
                            ))}
                            {highlightCount > 3 && (
                                <Button
                                    variant="link"
                                    size="sm"
                                    className="h-auto p-0 text-xs"
                                    onClick={() => setShowAllHighlights(!showAllHighlights)}
                                >
                                    {showAllHighlights
                                        ? 'Show less'
                                        : `+${highlightCount - 3} more highlight${highlightCount - 3 !== 1 ? 's' : ''}`
                                    }
                                </Button>
                            )}
                        </div>
                    </div>
                )}

                {/* Tags Section */}
                <div className="border-t border-border pt-4 mt-4 space-y-2">
                    <div className="flex items-center justify-between mb-1">
                        <h4 className="font-semibold text-sm">Tags</h4>
                        <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                                <Button variant="outline" size="icon-sm" className="size-7" aria-label="Add tag"><Plus className="size-3.5" /></Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent className="w-80" align="start">
                                <TagSelector
                                    paperIds={[paper.id]}
                                    onTagsApplied={onTagsApplied}
                                />
                            </DropdownMenuContent>
                        </DropdownMenu>
                    </div>
                    <div className="flex flex-wrap gap-2 items-center">
                        {paper.tags?.map(tag => (
                            <span key={tag.id} className="group/tag inline-flex items-center rounded-md bg-brand/10 py-0.5 pr-0.5 pl-2 text-xs font-medium text-brand">
                                {tag.name}
                                <button
                                    type="button"
                                    aria-label={`Remove tag ${tag.name}`}
                                    onClick={() => handleRemoveTag(tag.id)}
                                    className="ml-0.5 rounded p-0.5 opacity-0 transition-opacity duration-150 group-hover/tag:opacity-100 hover:bg-brand/15 focus-visible:opacity-100 focus-visible:outline-none pointer-coarse:opacity-100"
                                >
                                    <X className="size-3" />
                                </button>
                            </span>
                        ))}
                    </div>
                </div>

                {/* Projects Section */}
                <div className="border-t border-border pt-4 mt-4">
                    <PaperProjects id={paper.id} view='compact' />
                </div>
            </div>
        </div>
    );
}
