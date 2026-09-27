"use client";

import { useMemo } from "react";
import Link from "next/link";
import useSWR from "swr";
import { api, unwrap } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/skeleton";

interface ProjectsPreviewProps {
    limit?: number;
}

import { ProjectCard } from "@/components/ProjectCard";
import { ArrowRight } from "lucide-react";

function ProjectCardSkeleton() {
    return (
        <div className="flex items-center gap-4 p-4 rounded-xl border border-border/50 bg-card">
            <Skeleton className="w-10 h-10 rounded-lg" />
            <div className="flex-1 space-y-2">
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-3 w-24" />
            </div>
        </div>
    );
}

export function ProjectsPreview({ limit = 4 }: ProjectsPreviewProps) {
    const { data, isLoading } = useSWR(
        ["/api/projects", { detailed: true, limit: 3 }],
        () => unwrap(api.GET("/api/projects", { params: { query: { detailed: true, limit: 3 } } })),
        { onError: (error) => console.error("Error fetching projects:", error) },
    );
    // Sort by updated_at and take top N
    const projects = useMemo(
        () =>
            [...(data ?? [])]
                .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
                .slice(0, limit),
        [data, limit],
    );

    if (isLoading) {
        return (
            <div className="space-y-3">
                <div className="flex items-center justify-between">
                    <Skeleton className="h-6 w-32" />
                    <Skeleton className="h-4 w-24" />
                </div>
                {[...Array(3)].map((_, i) => (
                    <ProjectCardSkeleton key={i} />
                ))}
            </div>
        );
    }

    if (projects.length === 0) {
        return null; // Don't show section if no projects
    }

    return (
        <div className="space-y-3 sm:space-y-4">
            <div className="flex items-center justify-between">
                <h2 className="text-base font-semibold sm:text-lg">Active projects</h2>
                <Link
                    href="/projects"
                    className="-mr-2 flex h-9 items-center gap-1 rounded-md px-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
                >
                    View all
                    <ArrowRight className="h-3.5 w-3.5" />
                </Link>
            </div>

            <div className="grid gap-2 sm:grid-cols-2 sm:gap-3">
                {projects.map((project, i) => (
                    <div key={project.id} className="animate-rise-in" style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}>
                        <ProjectCard project={project} compact={true} />
                    </div>
                ))}
            </div>
        </div>
    );
}
