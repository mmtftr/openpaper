"use client";

import { mutate as mutateSWR } from "swr";
import { PROJECTS_LIST_KEY } from "@/hooks/useProjects";
import { Loader, FolderKanban, Plus, X } from 'lucide-react';
import Link from 'next/link';
import React, { useState, type FormEvent } from 'react';
import useSWR from 'swr';
import { toast } from "sonner";
import { api, unwrap, type Schemas } from '@/lib/api/client';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { ProjectCard } from './ProjectCard';

type Project = Schemas["ProjectResponse"];

/**
 * The projects a paper belongs to. Shared SWR key, so the paper header's
 * breadcrumb follows adds / removes made in the info panel right away.
 */
export function usePaperProjects(paperId: string | null | undefined) {
    return useSWR(
        paperId ? ["/api/projects/papers/from/{paper_id}", paperId] : null,
        ([, id]) => unwrap(api.GET("/api/projects/papers/from/{paper_id}", { params: { path: { paper_id: id } } })),
        {
            onError: (err: unknown) => {
                console.error("Error fetching projects", err);
                toast.error("Error fetching projects", { id: "paper-projects-fetch-error" });
            },
        },
    );
}

interface PaperProjectsProps {
    id: string;
    /** `full`: manage membership (the paper info panel); `compact`: just list them. */
    view?: 'full' | 'compact';
}

export function PaperProjects({ id, view = 'full' }: PaperProjectsProps) {
    const { data: projects = [], isLoading, mutate: mutateProjects } = usePaperProjects(id);

    const handleUnlink = async (projectId: string) => {
        try {
            await unwrap(api.DELETE("/api/projects/papers/{project_id}/{project_paper_id}", {
                params: { path: { project_id: projectId, project_paper_id: id } },
            }));
            toast.success("Removed from project");
            mutateProjects(prev => (prev ?? []).filter(p => p.id !== projectId), { revalidate: false });
        } catch (error) {
            console.error("Failed to unlink paper from project", error);
            toast.error("Failed to remove the paper from the project.");
        }
    };

    if (view === 'compact') {
        if (isLoading || projects.length === 0) return null;
        return (
            <div className="space-y-4">
                <h3 className="text-lg font-semibold">Projects</h3>
                <div className="space-y-2">
                    {projects.map(project => (
                        <ProjectCard
                            key={project.id}
                            project={project}
                            compact={true}
                            onUnlink={() => handleUnlink(project.id)}
                        />
                    ))}
                </div>
            </div>
        );
    }

    return (
        <ManageProjects
            paperId={id}
            projects={projects}
            isLoading={isLoading}
            onUnlink={handleUnlink}
            onAdded={(project) =>
                mutateProjects(
                    prev => (prev ?? []).some(p => p.id === project.id) ? prev : [...(prev ?? []), project],
                    { revalidate: false },
                )
            }
        />
    );
}

/**
 * Compact, inline membership editor: remove (with an inline confirm), add to
 * an existing project, or start a new one — no nested dialogs, so it can live
 * inside a popover or drawer.
 */
function ManageProjects({
    paperId,
    projects,
    isLoading,
    onUnlink,
    onAdded,
}: {
    paperId: string;
    projects: Project[];
    isLoading: boolean;
    onUnlink: (projectId: string) => Promise<void>;
    onAdded: (project: Project) => void;
}) {
    const [confirmingId, setConfirmingId] = useState<string | null>(null);
    const [adding, setAdding] = useState(false);
    const [busyId, setBusyId] = useState<string | null>(null);
    const [query, setQuery] = useState('');

    const { data: allProjects = [], isLoading: isLoadingAll } = useSWR(
        adding ? ["/api/projects", { detailed: true }] : null,
        () => unwrap(api.GET("/api/projects", { params: { query: { detailed: true } } })),
    );

    const addTo = async (project: Project) => {
        setBusyId(project.id);
        try {
            await unwrap(api.POST("/api/projects/papers/{project_id}", {
                params: { path: { project_id: project.id } },
                body: { paper_ids: [paperId] },
            }));
            onAdded(project);
            setAdding(false);
            toast.success(`Added to ${project.title || "project"}`);
        } catch (error) {
            console.error("Failed to add paper to project", error);
            toast.error("Failed to add the paper to the project.");
        } finally {
            setBusyId(null);
        }
    };

    const createWithPaper = async (event: FormEvent) => {
        event.preventDefault();
        const title = query.trim();
        if (!title) return;
        setBusyId("new");
        try {
            const project = await unwrap(api.POST("/api/projects", { body: { title, description: "" } }));
            void mutateSWR(PROJECTS_LIST_KEY);
            await unwrap(api.POST("/api/projects/papers/{project_id}", {
                params: { path: { project_id: project.id } },
                body: { paper_ids: [paperId] },
            }));
            onAdded(project);
            setQuery('');
            setAdding(false);
            toast.success(`Created ${title}`);
        } catch (error) {
            console.error("Failed to create project", error);
            toast.error("Failed to create the project.");
        } finally {
            setBusyId(null);
        }
    };

    const candidates = allProjects.filter(p => !projects.some(pp => pp.id === p.id));
    const q = query.trim().toLowerCase();
    const matches = q
        ? candidates.filter(p => p.title?.toLowerCase().includes(q) || p.description?.toLowerCase().includes(q))
        : candidates;
    const exactMatch = allProjects.some(p => p.title?.trim().toLowerCase() === q);

    if (isLoading) {
        return (
            <div className="flex items-center gap-2 py-1 text-xs text-muted-foreground">
                <Loader className="size-3.5 animate-spin" /> Loading projects…
            </div>
        );
    }

    return (
        <div className="space-y-2">
            {projects.length > 0 ? (
                <ul className="space-y-0.5">
                    {projects.map(project => (
                        <li key={project.id}>
                            {confirmingId === project.id ? (
                                <div className="flex items-center gap-1.5 rounded-md bg-muted/60 py-1 pl-2 pr-1 text-xs">
                                    <span className="min-w-0 flex-1 truncate">Remove from {project.title}?</span>
                                    <Button
                                        size="sm"
                                        variant="destructive"
                                        className="h-7 px-2 text-xs"
                                        onClick={async () => {
                                            await onUnlink(project.id);
                                            setConfirmingId(null);
                                        }}
                                    >
                                        Remove
                                    </Button>
                                    <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={() => setConfirmingId(null)}>
                                        Cancel
                                    </Button>
                                </div>
                            ) : (
                                <div className="group flex items-center gap-1 rounded-md hover:bg-accent/60">
                                    <Link
                                        href={`/projects/${project.id}`}
                                        className="flex min-w-0 flex-1 items-center gap-2 rounded-md py-1.5 pl-2 text-sm focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-brand/30 max-md:py-2"
                                    >
                                        <FolderKanban className="size-3.5 shrink-0 text-muted-foreground" />
                                        <span className="truncate">{project.title}</span>
                                        {project.num_papers != null && (
                                            <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                                                {project.num_papers}
                                            </span>
                                        )}
                                    </Link>
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="size-7 shrink-0 text-muted-foreground hover:text-foreground pointer-fine:opacity-0 pointer-fine:group-hover:opacity-100 focus-visible:opacity-100 max-md:size-9"
                                        aria-label={`Remove from ${project.title}`}
                                        title="Remove from project"
                                        onClick={() => setConfirmingId(project.id)}
                                    >
                                        <X className="size-3.5" />
                                    </Button>
                                </div>
                            )}
                        </li>
                    ))}
                </ul>
            ) : (
                <p className="text-xs text-muted-foreground">Not in any project yet.</p>
            )}

            {adding ? (
                <form onSubmit={createWithPaper} className="space-y-1.5 rounded-md border border-border/70 p-1.5">
                    <Input
                        autoFocus
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        onKeyDown={(e) => {
                            if (e.key === "Escape" && query) {
                                e.stopPropagation();
                                setQuery('');
                            }
                        }}
                        placeholder="Find or create a project…"
                        aria-label="Find or create a project"
                        className="h-8 text-sm"
                    />
                    <ul className="max-h-40 space-y-0.5 overflow-y-auto">
                        {isLoadingAll ? (
                            <li className="flex items-center gap-2 px-2 py-1.5 text-xs text-muted-foreground">
                                <Loader className="size-3.5 animate-spin" /> Loading…
                            </li>
                        ) : (
                            matches.map(project => (
                                <li key={project.id}>
                                    <button
                                        type="button"
                                        onClick={() => addTo(project)}
                                        disabled={busyId !== null}
                                        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent focus-visible:bg-accent focus-visible:outline-none disabled:opacity-60 max-md:py-2"
                                    >
                                        {busyId === project.id ? (
                                            <Loader className="size-3.5 shrink-0 animate-spin" />
                                        ) : (
                                            <Plus className="size-3.5 shrink-0 text-muted-foreground" />
                                        )}
                                        <span className="truncate">{project.title}</span>
                                    </button>
                                </li>
                            ))
                        )}
                        {q && !exactMatch && (
                            <li>
                                <button
                                    type="submit"
                                    disabled={busyId !== null}
                                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-brand transition-colors hover:bg-accent focus-visible:bg-accent focus-visible:outline-none disabled:opacity-60 max-md:py-2"
                                >
                                    {busyId === "new" ? (
                                        <Loader className="size-3.5 shrink-0 animate-spin" />
                                    ) : (
                                        <FolderKanban className="size-3.5 shrink-0" />
                                    )}
                                    <span className="truncate">New project “{query.trim()}”</span>
                                </button>
                            </li>
                        )}
                        {!isLoadingAll && !q && candidates.length === 0 && (
                            <li className="px-2 py-1.5 text-xs text-muted-foreground">
                                Type a name to create a project.
                            </li>
                        )}
                    </ul>
                </form>
            ) : null}

            <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground max-md:h-9"
                onClick={() => {
                    setAdding(a => !a);
                    setQuery('');
                }}
            >
                {adding ? "Done" : (<><Plus className="size-3.5" />Add to project</>)}
            </Button>
        </div>
    );
}
