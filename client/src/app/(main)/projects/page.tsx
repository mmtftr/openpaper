"use client";
import { useEffect, useState, Suspense, useMemo } from "react";
import useSWR, { useSWRConfig } from "swr";
import { SIDEBAR_PROJECTS_KEY } from "@/hooks/useProjects";
import { useRouter } from "next/navigation";
import { ProjectCard } from "@/components/ProjectCard";
import { Button } from "@/components/ui/button";
import { api, unwrap } from "@/lib/api/client";
import { PlusCircle, Target, BookOpen, FileText, Search, X, Plus } from "lucide-react";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { useAuth } from "@/lib/auth";
import LoadingIndicator from "@/components/utils/Loading";
import { CreateProjectDialog } from "@/components/CreateProjectDialog";

function ProjectsPage() {
	const { user, loading: userLoading } = useAuth();
	const router = useRouter();
	const {
		data: projectsData,
		error: fetchError,
		isLoading: isLoadingProjects,
		mutate: mutateProjects,
	} = useSWR(
		!userLoading && user ? ["/api/projects", { detailed: true }] : null,
		() => unwrap(api.GET("/api/projects", { params: { query: { detailed: true } } })),
		{ onError: (err) => console.error(err) },
	);
	const projects = useMemo(() => projectsData ?? [], [projectsData]);
	const isLoading = userLoading || !user || isLoadingProjects;
	const error = fetchError ? "Failed to fetch projects. Please try again." : null;
	const [isCreateProjectOpen, setCreateProjectOpen] = useState(false);

	const [searchQuery, setSearchQuery] = useState("");

	const clearAllFilters = () => {
		setSearchQuery("");
	};

	const filteredProjects = useMemo(() => {
		return projects.filter((project) => {
			// Search filter
			if (searchQuery.trim()) {
				const query = searchQuery.toLowerCase();
				const matchesTitle = project.title?.toLowerCase().includes(query);
				const matchesDescription = project.description?.toLowerCase().includes(query);
				if (!matchesTitle && !matchesDescription) return false;
			}

			return true;
		});
	}, [projects, searchQuery]);

	const hasActiveFilters = searchQuery.trim() !== "";

	const getProjects = () => {
		mutateProjects();
	};

	const { mutate: globalMutate } = useSWRConfig();
	const handleCreateProject = async (title: string, description: string) => {
		try {
			const project = await unwrap(api.POST("/api/projects", {
				body: { title, description },
			}));
			setCreateProjectOpen(false);
			void globalMutate(SIDEBAR_PROJECTS_KEY);
			router.push(`/projects/${project.id}`);
		} catch (err) {
			console.error(err);
			toast.error("Failed to create project. Please try again.");
		}
	};

	useEffect(() => {
		if (userLoading) return;
		if (!user) {
			localStorage.setItem('returnTo', window.location.pathname);
			router.push("/login");
		}
	}, [userLoading, user, router]);

	// Empty state component
	const EmptyState = () => (
		<div className="mx-auto flex min-h-[60vh] max-w-2xl animate-rise-in flex-col items-center justify-center px-4 py-12 text-center">
			<h2 className="text-2xl font-bold text-foreground mb-3">
				Organize Your Research
			</h2>
			<p className="text-muted-foreground mb-8 max-w-md">
				Group papers by topic, get focused AI assistance, and streamline your literature reviews.
			</p>

			{/* CTA buttons */}
			<div className="flex flex-col sm:flex-row gap-3 mb-12">
					<Button
						size="lg"
						className="bg-brand text-brand-foreground hover:bg-brand/90"
						onClick={() => setCreateProjectOpen(true)}
					>
						<PlusCircle className="mr-2 h-4 w-4" />
						Create your first project
					</Button>
			</div>

			{/* Feature highlights */}
			<div className="flex items-start justify-center gap-6 text-brand sm:gap-8">
				<div className="flex flex-col items-center gap-1 max-w-24">
					<Target className="h-5 w-5" />
					<span className="text-xs font-medium text-foreground">Focus</span>
					<span className="text-xs text-muted-foreground text-center">Organize by topic or goal</span>
				</div>
				<div className="flex flex-col items-center gap-1 max-w-24">
					<BookOpen className="h-5 w-5" />
					<span className="text-xs font-medium text-foreground">AI Chat</span>
					<span className="text-xs text-muted-foreground text-center">Context-aware assistance</span>
				</div>
				<div className="flex flex-col items-center gap-1 max-w-24">
					<FileText className="h-5 w-5" />
					<span className="text-xs font-medium text-foreground">Research</span>
					<span className="text-xs text-muted-foreground text-center">Streamline lit reviews</span>
				</div>
			</div>
		</div>
	);

	return (
		<div className="mx-auto w-full max-w-6xl px-4 py-5 sm:px-6 sm:py-8 lg:px-8">
			<div className="mb-5 flex items-center justify-between gap-3 sm:mb-6">
				<div className="min-w-0">
					<h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
					{projects.length > 0 && (
						<p className="mt-0.5 text-sm text-muted-foreground tabular-nums">
							{projects.length} project{projects.length !== 1 ? "s" : ""}
						</p>
					)}
				</div>
				{projects.length > 0 && (
					<Button className="shrink-0 bg-brand text-brand-foreground hover:bg-brand/90" onClick={() => setCreateProjectOpen(true)}>
						<Plus />
						New project
					</Button>
				)}
			</div>

			{/* Search and Filters */}
			{projects.length > 0 && (
				<div className="mb-5 flex flex-wrap items-center gap-2 sm:mb-6">
					<div className="relative w-full sm:max-w-sm">
						<Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
						<Input
							type="text"
							aria-label="Search projects"
							placeholder="Search projects…"
							value={searchQuery}
							onChange={(e) => setSearchQuery(e.target.value)}
							className="h-10 pl-9 sm:h-9"
						/>
					</div>
					{hasActiveFilters && (
						<>
							<button
								onClick={clearAllFilters}
								className="inline-flex h-9 items-center gap-1 rounded-full px-2.5 text-sm text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
							>
								<X className="h-3.5 w-3.5" />
								Clear
							</button>
							<span className="text-sm text-muted-foreground tabular-nums">
								{filteredProjects.length} of {projects.length}
							</span>
						</>
					)}
				</div>
			)}
			{isLoading ? (
				<div className="flex items-center justify-center py-12">
					<LoadingIndicator />
					<span className="ml-3 text-muted-foreground">Retrieving your projects...</span>
				</div>
			) : error ? (
				<div className="flex flex-col items-center justify-center py-12">
					<p className="text-destructive mb-4">{error}</p>
					<Button onClick={getProjects} variant="outline">
						Try Again
					</Button>
				</div>
			) : projects.length === 0 ? (
				<EmptyState />
			) : filteredProjects.length === 0 ? (
				<div className="flex animate-rise-in flex-col items-center justify-center py-16 text-center">
					<div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-secondary">
						<Search className="h-6 w-6 text-muted-foreground" />
					</div>
					<h3 className="mb-2 text-lg font-semibold">No matching projects</h3>
					<p className="mb-4 max-w-md text-muted-foreground">
						{searchQuery.trim()
							? `No projects found matching "${searchQuery}"`
							: "No projects match the selected filters"}
					</p>
					<Button variant="outline" onClick={clearAllFilters}>
						<X className="mr-2 h-4 w-4" />
						Clear Filters
					</Button>
				</div>
			) : (
				<div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 xl:grid-cols-3">
					{filteredProjects.map((project, i) => (
						<div key={project.id} className="animate-rise-in" style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}>
							<ProjectCard project={project} onProjectUpdate={getProjects} />
						</div>
					))}
					{/* New Project tile */}
					{!hasActiveFilters && (
						<button
							type="button"
							onClick={() => setCreateProjectOpen(true)}
							className="group hidden min-h-40 flex-col items-center justify-center rounded-xl border-2 border-dashed border-border/70 text-muted-foreground transition-[border-color,background-color,color] duration-200 ease-out-soft hover:border-brand/40 hover:bg-brand/[0.04] hover:text-foreground focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-brand/30 motion-safe:active:scale-[0.99] sm:flex"
						>
							<span className="mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-muted transition-colors group-hover:bg-brand/10 group-hover:text-brand">
								<Plus className="h-5 w-5" />
							</span>
							<span className="text-sm font-medium">New project</span>
						</button>
					)}
				</div>
			)}

			<CreateProjectDialog
				open={isCreateProjectOpen}
				onOpenChange={setCreateProjectOpen}
				onSubmit={handleCreateProject}
			/>
		</div>
	);
}

export default function Projects() {
	return (
		<Suspense fallback={<div className="flex items-center justify-center py-12">
			<LoadingIndicator />
			<span className="ml-3 text-muted-foreground">Loading...</span>
		</div>}>
			<ProjectsPage />
		</Suspense>
	)
}
