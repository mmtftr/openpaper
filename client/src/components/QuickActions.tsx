"use client";

import { mutate as mutateSWR } from "swr";
import { SIDEBAR_PROJECTS_KEY } from "@/hooks/useProjects";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Upload, FolderPlus, Globe2 } from "lucide-react";
import { UploadModal } from "@/components/UploadModal";
import { CreateProjectDialog } from "@/components/CreateProjectDialog";
import { api, unwrap } from "@/lib/api/client";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

interface QuickActionCardProps {
    icon: React.ReactNode;
    title: string;
    /** Label for the compact phone tile. */
    shortTitle: string;
    description: string;
    onClick: () => void;
    variant?: "default" | "primary";
}

// Phones: a compact 3-up row of icon tiles. From `sm`: lighter cards with
// the description.
function QuickActionCard({ icon, title, shortTitle, description, onClick, variant = "default" }: QuickActionCardProps) {
    const primary = variant === "primary";
    return (
        <button
            type="button"
            onClick={onClick}
            className={cn(
                "group flex w-full flex-col items-center justify-center gap-2 rounded-xl border px-2 py-3 text-center",
                "sm:flex-row sm:justify-start sm:gap-3.5 sm:p-4 sm:text-left",
                "transition-[transform,box-shadow,border-color,background-color] duration-200 ease-out-soft",
                "hover:-translate-y-0.5 hover:shadow-md motion-safe:active:translate-y-0 motion-safe:active:scale-[0.98]",
                "focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-brand/30",
                primary
                    ? "border-brand/25 bg-brand/[0.07] hover:border-brand/40 hover:bg-brand/10"
                    : "border-border/60 bg-card hover:border-border"
            )}
        >
            <span className={cn(
                "flex size-9 shrink-0 items-center justify-center rounded-lg transition-colors duration-200 sm:size-10",
                primary
                    ? "bg-brand text-brand-foreground shadow-sm"
                    : "bg-muted text-muted-foreground group-hover:text-foreground"
            )}>
                {icon}
            </span>
            <span className="min-w-0">
                <span className={cn("block text-xs font-medium sm:hidden", primary && "text-brand")}>{shortTitle}</span>
                <span className="hidden font-semibold text-foreground sm:block">{title}</span>
                <span className="mt-0.5 hidden text-sm text-muted-foreground sm:line-clamp-1">{description}</span>
            </span>
        </button>
    );
}

interface QuickActionsProps {
    onUploadComplete?: () => void;
    onProjectCreated?: () => void;
    onUploadStart?: (files: File[]) => void;
    onUrlImportStart?: (url: string) => void;
}

export function QuickActions({ onUploadComplete, onProjectCreated, onUploadStart, onUrlImportStart }: QuickActionsProps) {
    const router = useRouter();
    const [isUploadModalOpen, setUploadModalOpen] = useState(false);
    const [isCreateProjectOpen, setCreateProjectOpen] = useState(false);

    const handleUploadClick = () => {
        setUploadModalOpen(true);
    };

    // The upload modal opens the paper itself; just refresh the lists.
    const handleUploadComplete = () => {
        onUploadComplete?.();
    };

    const handleCreateProject = async (title: string, description: string) => {
        try {
            const response = await unwrap(api.POST("/api/projects", {
                body: { title, description },
            }));
            void mutateSWR(SIDEBAR_PROJECTS_KEY);

            if (response?.id) {
                try {
                    await router.push(`/projects/${response.id}`);
                    toast.success("Project created successfully!");
                    setCreateProjectOpen(false);
                    onProjectCreated?.();
                } catch (navError) {
                    console.error("Navigation error:", navError);
                    toast.error("Project created, but failed to navigate. Please try again.");
                }
            } else {
                toast.error("Failed to create project. Please try again.");
            }
        } catch (error) {
            console.error("Error creating project:", error);
            toast.error("Failed to create project. Please try again.");
        }
    };

    const handleFindPapers = () => {
        router.push("/discover");
    };

    return (
        <>
            <div className="grid w-full grid-cols-3 gap-2 sm:gap-4">
                <QuickActionCard
                    icon={<Upload className="h-5 w-5" />}
                    title="Upload Paper"
                    shortTitle="Upload"
                    description="Start your next study"
                    onClick={handleUploadClick}
                    variant="primary"
                />
                <QuickActionCard
                    icon={<FolderPlus className="h-5 w-5" />}
                    title="New Project"
                    shortTitle="New project"
                    description="Organize your research"
                    onClick={() => setCreateProjectOpen(true)}
                />
                <QuickActionCard
                    icon={<Globe2 className="h-5 w-5" />}
                    title="Discover Research"
                    shortTitle="Discover"
                    description="Find papers across academic sources"
                    onClick={handleFindPapers}
                />
            </div>

            <UploadModal
                open={isUploadModalOpen}
                uploadLimit={1}
                onOpenChange={setUploadModalOpen}
                onUploadComplete={handleUploadComplete}
                onUploadStart={onUploadStart}
                onUrlImportStart={onUrlImportStart}
            />

            <CreateProjectDialog
                open={isCreateProjectOpen}
                onOpenChange={setCreateProjectOpen}
                onSubmit={handleCreateProject}
            />
        </>
    );
}
