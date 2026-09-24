"use client";

import {
    Loader,
    ArrowRight,
    CirclePlus,
} from 'lucide-react';
import Link from 'next/link';
import React, { useState } from 'react';
import useSWR from 'swr';
import { useRouter } from 'next/navigation';
import { toast } from "sonner";
import { api, unwrap } from '@/lib/api/client';
import { Button } from './ui/button';
import { CreateProjectDialog } from '@/components/CreateProjectDialog';
import { Input } from './ui/input';
import { ProjectCard } from './ProjectCard';

interface PaperProjectsProps {
    id: string;
    view?: 'full' | 'compact';
}

export function PaperProjects({ id, view = 'full' }: PaperProjectsProps) {
    const [addingToProjectId, setAddingToProjectId] = useState<string | null>(null);
    const [isCreateProjectDialogOpen, setCreateProjectDialogOpen] = useState(false);
    const [searchQuery, setSearchQuery] = useState('');
    const router = useRouter();

    const onFetchError = (err: unknown) => {
        console.error("Error fetching projects", err);
        toast.error("Error fetching projects", { id: "paper-projects-fetch-error" });
    };
    // Projects this paper belongs to, and all projects (for "Add to Projects").
    const { data: projects = [], isLoading: isLoadingPaperProjects, mutate: mutateProjects } = useSWR(
        id ? ["/api/projects/papers/from/{paper_id}", id] : null,
        ([, paperId]) => unwrap(api.GET("/api/projects/papers/from/{paper_id}", { params: { path: { paper_id: paperId } } })),
        { onError: onFetchError },
    );
    const { data: allProjects = [], isLoading: isLoadingAllProjects } = useSWR(
        id ? ["/api/projects", { detailed: true }] : null,
        () => unwrap(api.GET("/api/projects", { params: { query: { detailed: true } } })),
        { onError: onFetchError },
    );
    const isLoadingProjects = isLoadingPaperProjects || isLoadingAllProjects;

    const handleUnlink = async (projectId: string) => {
        try {
            await unwrap(api.DELETE("/api/projects/papers/{project_id}/{project_paper_id}", {
                params: { path: { project_id: projectId, project_paper_id: id } },
            }));
            toast.success("Paper unlinked from project successfully!");
            mutateProjects(prev => (prev ?? []).filter(p => p.id !== projectId), { revalidate: false });
        } catch (error) {
            console.error("Failed to unlink paper from project", error);
            toast.error("Failed to unlink paper from project.");
        }
    };

    const handleAddPaperToProject = async (projectId: string) => {
        setAddingToProjectId(projectId);
        try {
            await unwrap(api.POST("/api/projects/papers/{project_id}", {
                params: { path: { project_id: projectId } },
                body: { paper_ids: [id] },
            }));
            toast.success("Paper added to project successfully!");

            const projectToAdd = allProjects.find(p => p.id === projectId);
            if (projectToAdd && !projects.some(p => p.id === projectId)) {
                mutateProjects(prev => [...(prev ?? []), projectToAdd], { revalidate: false });
            }
        } catch (error) {
            console.error("Failed to add paper to project", error);
            toast.error("Failed to add paper to project.");
        } finally {
            setAddingToProjectId(null);
        }
    };

    const handleCreateProjectSubmit = async (title: string, description: string) => {
        try {
            const project = await unwrap(api.POST("/api/projects", {
                body: { title, description },
            }));
            toast.success("Project created successfully!");

            await unwrap(api.POST("/api/projects/papers/{project_id}", {
                params: { path: { project_id: project.id } },
                body: { paper_ids: [id] },
            }));
            toast.success("Paper added to project successfully!");


            router.push(`/projects/${project.id}`);
        } catch (error) {
            console.error("Failed to create project", error);
            toast.error("Failed to create project.");
        } finally {
            setCreateProjectDialogOpen(false);
        }
    };

    const projectsToAdd = allProjects.filter(p => !projects.some(pp => pp.id === p.id));
    const filteredProjectsToAdd = projectsToAdd.filter(project =>
        project.title?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (project.description && project.description.toLowerCase().includes(searchQuery.toLowerCase()))
    );


    if (view === 'compact' && !isLoadingProjects && projects.length === 0) {
        return null;
    }

    return (
        <div className="space-y-4">
            {view === 'full' && (
                <CreateProjectDialog
                    open={isCreateProjectDialogOpen}
                    onOpenChange={setCreateProjectDialogOpen}
                    onSubmit={handleCreateProjectSubmit}
                />
            )}
            {isLoadingProjects ? (
                <div className="flex items-center justify-center py-4">
                    <Loader className="animate-spin mr-2 h-4 w-4" />
                </div>
            ) : projects.length > 0 ? (
                <>
                    <h3 className="text-lg font-semibold">Projects</h3>
                    {view === 'full' && <p className="text-sm text-muted-foreground">This paper is a member of the following projects.</p>}
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
                </>
            ) : (
                <>
                    {view === 'full' && (
                        <div className="text-left">
                            <p className="text-sm text-muted-foreground">Projects help you organize your research. Group papers together to analyze them as a collection.
                            </p>
                            <Link href="/projects" className="block underline">View all projects{" "} <ArrowRight className='inline w-4 h-4' /></Link>
                            <Button
                                onClick={() => setCreateProjectDialogOpen(true)}
                                className="mt-4 w-full"
                            >
                                Create a Project with this Paper
                            </Button>
                        </div>
                    )}
                </>
            )}
            {view === 'full' && projectsToAdd.length > 0 && (
                <div className="pt-4 mt-4 border-t">
                    <h3 className="text-lg font-semibold mb-2">Add to Projects</h3>
                    {isLoadingProjects ? (
                        <div className="flex items-center justify-center py-4">
                            <Loader className="animate-spin mr-2 h-4 w-4" />
                        </div>
                    ) : (
                        <div className="space-y-2">
                            <Input
                                placeholder="Search projects..."
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                                className="mb-2"
                            />
                            <div className="space-y-2 max-h-60 overflow-y-auto">
                                {filteredProjectsToAdd.map(project => (
                                    <div key={project.id} className="flex items-center justify-between p-2 border rounded-md">
                                        <div className='pr-2'>
                                            <div className="font-semibold">{project.title}</div>
                                            {project.description && <div className="text-sm text-muted-foreground">{project.description}</div>}
                                        </div>
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            onClick={() => handleAddPaperToProject(project.id)}
                                            disabled={addingToProjectId === project.id}
                                            className="flex-shrink-0"
                                        >
                                            {addingToProjectId === project.id ? (
                                                <Loader className="animate-spin h-4 w-4" />
                                            ) : (
                                                <CirclePlus className="h-4 w-4" />
                                            )}
                                        </Button>
                                    </div>
                                ))}
                            </div>
                            {filteredProjectsToAdd.length === 0 && (
                                <p className="text-sm text-muted-foreground text-center">No projects found.</p>
                            )}
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}
