"use client"

import { mutate as mutateSWR } from "swr";
import { SIDEBAR_PROJECTS_KEY } from "@/hooks/useProjects";
import { api, unwrap } from "@/lib/api/client";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";
import { Upload, Highlighter, Quote, FolderKanban } from "lucide-react";
import { toast } from "sonner";
import { LibraryTable, LibrarySkeleton, type LibraryPaper } from "@/components/LibraryTable";
import { cn } from "@/lib/utils";
import { CreateProjectDialog } from "@/components/CreateProjectDialog";
import { useRouter } from "next/navigation";
import { UploadModal } from "@/components/UploadModal";
import { usePapers } from "@/hooks/usePapers";

const PAGE_CLASS = "mx-auto flex w-full min-w-0 flex-1 flex-col gap-4 px-4 pt-4 md:h-app md:flex-none md:px-6 md:pt-6 md:pb-4";

const PageSkeleton = () => (
    <div className={PAGE_CLASS}>
        <div className="flex items-center justify-between gap-3">
            <Skeleton className="h-8 w-40" />
            <Skeleton className="size-10 sm:h-9 sm:w-28" />
        </div>
        <LibrarySkeleton />
    </div>
);

function LibraryEmptyState({ onUploadClick }: { onUploadClick: () => void }) {
    const [isDragging, setIsDragging] = useState(false);

    const handleDragEnter = (e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(true);
    };

    const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.stopPropagation();
        if (e.relatedTarget && !(e.currentTarget.contains(e.relatedTarget as Node))) {
            setIsDragging(false);
        } else if (!e.relatedTarget) {
            setIsDragging(false);
        }
    };

    const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(true);
    };

    const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
        e.preventDefault();
        e.stopPropagation();
        setIsDragging(false);

        const files = Array.from(e.dataTransfer.files).filter(
            file => file.type === 'application/pdf'
        );

        if (files.length > 0) {
            onUploadClick();
        }

        if (e.dataTransfer) {
            e.dataTransfer.items.clear();
        }
    };

    return (
        <div
            className={cn(
                "mx-auto flex w-full max-w-2xl flex-col items-center justify-center rounded-2xl px-4 py-16 text-center transition-colors duration-200 ease-out-soft md:h-full",
                isDragging && "bg-brand/5 outline-2 outline-offset-4 outline-brand/60 outline-dashed",
            )}
            onDragEnter={handleDragEnter}
            onDragLeave={handleDragLeave}
            onDragOver={handleDragOver}
            onDrop={handleDrop}
        >
            <div className="mb-5 flex size-14 animate-rise-in items-center justify-center rounded-2xl bg-brand/10 text-brand">
                <Upload className="size-6" />
            </div>
            <h2 className="mb-2 animate-rise-in text-2xl font-semibold tracking-tight [animation-delay:40ms]">Build your research library</h2>
            <p className="mb-8 max-w-md animate-rise-in text-muted-foreground [animation-delay:80ms]">
                Drop a PDF here or upload one to get started.
            </p>

            <Button
                size="lg"
                className="animate-rise-in bg-brand text-brand-foreground [animation-delay:120ms] hover:bg-brand/90"
                onClick={onUploadClick}
            >
                <Upload className="h-4 w-4" />
                Upload your first paper
            </Button>

            <div className="mt-12 grid w-full max-w-md animate-rise-in grid-cols-3 gap-4 [animation-delay:160ms]">
                {[
                    { icon: Highlighter, title: "Annotations", text: "Highlight and take notes" },
                    { icon: FolderKanban, title: "Projects", text: "Organize by topic" },
                    { icon: Quote, title: "Citations", text: "Export in any format" },
                ].map(({ icon: Icon, title, text }) => (
                    <div key={title} className="flex flex-col items-center gap-1">
                        <Icon className="mb-1 size-5 text-brand" />
                        <span className="text-xs font-medium">{title}</span>
                        <span className="text-xs text-muted-foreground">{text}</span>
                    </div>
                ))}
            </div>
        </div>
    );
}

function PapersPageContent() {
    const { papers, isLoading, mutate } = usePapers();
    const [filteredPapers, setFilteredPapers] = useState<LibraryPaper[]>([]);
    const router = useRouter();
    const [isCreateProjectDialogOpen, setCreateProjectDialogOpen] = useState(false);
    const [papersForNewProject, setPapersForNewProject] = useState<LibraryPaper[]>([]);
    const [isUploadModalOpen, setUploadModalOpen] = useState(false);

    const handleUploadClick = () => {
        setUploadModalOpen(true);
    };

    useEffect(() => {
        if (papers) {
            const sortedPapers = [...papers].sort((a, b) => {
                return new Date(b.created_at || "").getTime() - new Date(a.created_at || "").getTime();
            });
            setFilteredPapers(sortedPapers);
        }
    }, [papers]);

    const deletePaper = async (paperId: string) => {
        try {
            await unwrap(api.DELETE("/api/paper", {
                params: { query: { id: paperId } },
            }));
            setFilteredPapers(filteredPapers.filter((paper) => paper.id !== paperId));
            toast.success("Paper deleted successfully");
        } catch (error) {
            if (error instanceof Error && error.message) {
                toast.error(error.message);
                throw error;
            }
            toast.error("Failed to remove this paper.");
            throw error;
        }
    }

    const handleTableAction = (papers: LibraryPaper[], action: string) => {
        if (action !== "Make Project") return;

        if (papers.length === 0) {
            toast.info("Please select at least one paper to create a project.");
            return;
        }
        setPapersForNewProject(papers);
        setCreateProjectDialogOpen(true);
    };

    const handleCreateProjectSubmit = async (title: string, description: string) => {
        const paperIds = papersForNewProject.map(p => p.id);

        try {
            const project = await unwrap(api.POST("/api/projects", {
                body: { title, description },
            }));
            void mutateSWR(SIDEBAR_PROJECTS_KEY);
            toast.success("Project created successfully!");

            if (paperIds.length > 0) {
                await unwrap(api.POST("/api/projects/papers/{project_id}", {
                    params: { path: { project_id: project.id } },
                    body: { paper_ids: paperIds },
                }));
                toast.success("Papers added to project successfully!");
            }

            router.push(`/projects/${project.id}`);
        } catch (error) {
            console.error("Failed to create project", error);
            toast.error("Failed to create project.");
        } finally {
            setCreateProjectDialogOpen(false);
            setPapersForNewProject([]);
        }
    };

    if (isLoading) {
        return <PageSkeleton />;
    }

    const paperCount = papers?.length ?? 0;

    return (
        <div className={PAGE_CLASS}>
            <CreateProjectDialog
                open={isCreateProjectDialogOpen}
                onOpenChange={setCreateProjectDialogOpen}
                onSubmit={handleCreateProjectSubmit}
            />
            <UploadModal open={isUploadModalOpen} onOpenChange={setUploadModalOpen} onUploadComplete={() => { mutate(); }} />
            <div className="flex shrink-0 items-center justify-between gap-3">
                <div className="flex min-w-0 items-baseline gap-2.5">
                    <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">Library</h1>
                    {paperCount > 0 && (
                        <span className="text-sm text-muted-foreground tabular-nums">
                            {paperCount} {paperCount === 1 ? "paper" : "papers"}
                        </span>
                    )}
                </div>
                <Button onClick={handleUploadClick} aria-label="Upload paper" className="size-10 shrink-0 sm:h-9 sm:w-auto sm:px-4">
                    <Upload className="h-4 w-4" />
                    <span className="hidden sm:inline">Upload</span>
                </Button>
            </div>
            {papers && papers.length === 0 ? (
                <div className="flex-1 md:min-h-0">
                    <LibraryEmptyState onUploadClick={handleUploadClick} />
                </div>
            ) : (
                <LibraryTable
                    fillHeight
                    className="md:min-h-0 md:flex-1"
                    handleDelete={deletePaper}
                    selectable={true}
                    actionOptions={["Make Project"]}
                    onSelectFiles={handleTableAction}
                    onUploadClick={handleUploadClick}
                />
            )}
        </div>
    )
}

export default function PapersPage() {
    const { user, loading: authLoading } = useAuth();

    useEffect(() => {
        if (!authLoading && !user) {
            localStorage.setItem('returnTo', window.location.pathname);
            window.location.href = `/login`;
        }
    }, [authLoading, user]);

    if (authLoading || !user) {
        return <PageSkeleton />;
    }

    return <PapersPageContent />
}
