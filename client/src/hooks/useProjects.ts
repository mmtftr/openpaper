"use client";

import { useCallback } from "react";
import useSWR from "swr";
import { api, unwrap } from "@/lib/api/client";
import { Project, PaperItem } from "@/lib/schema";

// Compat: callers still hold the hand-written `Project` / `PaperItem`, which
// the generated responses don't match exactly (`string | null` vs `string`).

interface UseProjectsResult {
    projects: Project[];
    isLoading: boolean;
    error: Error | null;
    refetch: () => Promise<void>;
}

export function useProjects(): UseProjectsResult {
    const { data, error, isLoading, mutate } = useSWR("/api/projects", async () =>
        (await unwrap(api.GET("/api/projects"))) as Project[]
    );
    const refetch = useCallback(async () => {
        await mutate();
    }, [mutate]);

    return {
        projects: data ?? [],
        isLoading,
        error: error ?? null,
        refetch,
    };
}

interface UseProjectResult {
    project: Project | null;
    isLoading: boolean;
    error: Error | null;
    refetch: () => Promise<void>;
}

export function useProject(projectId?: string): UseProjectResult {
    const { data, error, isLoading, mutate } = useSWR(
        projectId ? ["/api/projects/{project_id}", projectId] : null,
        async ([, id]: [string, string]) =>
            (await unwrap(
                api.GET("/api/projects/{project_id}", { params: { path: { project_id: id } } })
            )) as Project
    );
    const refetch = useCallback(async () => {
        await mutate();
    }, [mutate]);

    return {
        project: data ?? null,
        isLoading,
        error: error ?? null,
        refetch,
    };
}

interface UseProjectPapersResult {
    papers: PaperItem[];
    isLoading: boolean;
    error: Error | null;
    refetch: () => Promise<void>;
}

export function useProjectPapers(projectId?: string): UseProjectPapersResult {
    const { data, error, isLoading, mutate } = useSWR(
        projectId ? ["/api/projects/papers/{project_id}", projectId] : null,
        async ([, id]: [string, string]) => {
            const { papers } = await unwrap(
                api.GET("/api/projects/papers/{project_id}", { params: { path: { project_id: id } } })
            );
            return papers as PaperItem[];
        }
    );
    const refetch = useCallback(async () => {
        await mutate();
    }, [mutate]);

    return {
        papers: data ?? [],
        isLoading,
        error: error ?? null,
        refetch,
    };
}
