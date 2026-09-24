"use client";
import { useEffect, useState, Suspense, useMemo } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ProjectCard } from "@/components/ProjectCard";
import { Button } from "@/components/ui/button";
import { Project } from "@/lib/schema";
import { fetchFromApi } from "@/lib/api";
import { PlusCircle, Target, BookOpen, FileText, Search, X, Plus } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { useAuth } from "@/lib/auth";
import LoadingIndicator from "@/components/utils/Loading";
import { CreateProjectDialog } from "@/components/CreateProjectDialog";

function ProjectsPage() {
	const [projects, setProjects] = useState<Project[]>([]);
	const [isLoading, setIsLoading] = useState(true);
	const { user, loading: userLoading } = useAuth();
	const [error, setError] = useState<string | null>(null);
	const router = useRouter();
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
				const matchesTitle = project.title.toLowerCase().includes(query);
				const matchesDescription = project.description?.toLowerCase().includes(query);
				if (!matchesTitle && !matchesDescription) return false;
			}

			return true;
		});
	}, [projects, searchQuery]);

	const hasActiveFilters = searchQuery.trim() !== "";

	const getProjects = async () => {
		try {
			const fetchedProjects = await fetchFromApi("/api/projects?detailed=true");
			setProjects(fetchedProjects);
		} catch (err) {
			setError("Failed to fetch projects. Please try again.");
			console.error(err);
		} finally {
			setIsLoading(false);
		}
	};

	const handleCreateProject = async (title: string, description: string) => {
		try {
			const project = await fetchFromApi("/api/projects", {
				method: "POST",
				body: JSON.stringify({ title, description }),
			});
			setCreateProjectOpen(false);
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
			return;
		}
		getProjects();
	}, [userLoading, user, router]);

	// Empty state component
	const EmptyState = () => (
		<div className="flex flex-col items-center justify-center py-16 px-4 text-center max-w-2xl mx-auto min-h-[60vh]">
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
						className="bg-primary hover:bg-primary/90"
						onClick={() => setCreateProjectOpen(true)}
					>
						<PlusCircle className="mr-2 h-4 w-4" />
						Create your first project
					</Button>
			</div>

			{/* Feature highlights */}
			<div className="flex items-center justify-center gap-8 text-blue-500">
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
		<div className="container mx-auto p-4">
			<div className="flex justify-between items-center mb-4">
				<h1 className="text-2xl font-bold">Projects</h1>
				<div className="flex gap-2">
					{projects.length > 0 && (
							<Button className="bg-blue-500 dark:text-card-foreground hover:bg-blue-600 dark:hover:bg-blue-400" onClick={() => setCreateProjectOpen(true)}>
								<PlusCircle className="mr-2" />
								New Project
							</Button>
					)}
				</div>
			</div>

			{/* Search and Filters */}
			{projects.length > 0 && (
				<div className="mb-4 space-y-3">
					{/* Search Input */}
					<div className="relative">
						<Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
						<Input
							type="text"
							placeholder="Search projects by title or description..."
							value={searchQuery}
							onChange={(e) => setSearchQuery(e.target.value)}
							className="pl-9 max-w-md"
						/>
					</div>

					<div className="flex flex-wrap items-center gap-2">
						{hasActiveFilters && (
							<>
								<button
									onClick={clearAllFilters}
									className="inline-flex items-center gap-1 px-2 py-1.5 rounded-full text-sm text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
								>
									<X className="h-3.5 w-3.5" />
									Clear
								</button>
								<span className="text-sm text-muted-foreground ml-2">
									{filteredProjects.length} of {projects.length} project{projects.length !== 1 ? "s" : ""}
								</span>
							</>
						)}
					</div>
				</div>
			)}
			{isLoading ? (
				<div className="flex items-center justify-center py-12">
					<LoadingIndicator />
					<span className="ml-3 text-gray-600">Retrieving your projects...</span>
				</div>
			) : error ? (
				<div className="flex flex-col items-center justify-center py-12">
					<p className="text-red-500 mb-4">{error}</p>
					<Button onClick={getProjects} variant="outline">
						Try Again
					</Button>
				</div>
			) : projects.length === 0 ? (
				<EmptyState />
			) : filteredProjects.length === 0 ? (
				<div className="flex flex-col items-center justify-center py-16 text-center">
					<div className="w-16 h-16 bg-secondary rounded-full flex items-center justify-center mb-4">
						<Search className="w-8 h-8 text-muted-foreground" />
					</div>
					<h3 className="text-lg font-semibold mb-2">No projects match your filters</h3>
					<p className="text-muted-foreground mb-4 max-w-md">
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
				<div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-4">
					{/* New Project Card */}
					{!hasActiveFilters && (
							<Card onClick={() => setCreateProjectOpen(true)} className="h-64 border-2 border-dashed border-border/50 hover:border-primary/50 bg-secondary/30 hover:bg-secondary/50 flex flex-col items-center justify-center text-muted-foreground hover:text-foreground transition-all duration-300 cursor-pointer group">
								<div className="w-12 h-12 rounded-full bg-muted group-hover:bg-primary/10 flex items-center justify-center mb-3 transition-colors">
									<Plus className="w-6 h-6 group-hover:text-primary transition-colors" />
								</div>
								<span className="font-medium">New Project</span>
								<span className="text-xs mt-1 text-muted-foreground">Create a new research project</span>
							</Card>
					)}
					{filteredProjects.map((project) => (
						<ProjectCard key={project.id} project={project} onProjectUpdate={getProjects} />
					))}
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
			<span className="ml-3 text-gray-600">Loading...</span>
		</div>}>
			<ProjectsPage />
		</Suspense>
	)
}
