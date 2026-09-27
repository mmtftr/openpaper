import Link from "next/link";
import { api, unwrap, type Schemas } from "@/lib/api/client";
import { Card, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { MoreHorizontal, ArrowRight, FileText, X } from "lucide-react";
import { useState } from "react";
import { useSWRConfig } from "swr";
import { PROJECTS_LIST_KEY } from "@/hooks/useProjects";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatDate } from "@/lib/utils";
import { toast } from "sonner";


export function ProjectCard({ project, onProjectUpdate, onUnlink, compact = false }: {
	project: Schemas["ProjectResponse"];
	onProjectUpdate?: () => void;
	onUnlink?: () => void;
	compact?: boolean;
}) {
	const [showDeleteAlert, setShowDeleteAlert] = useState(false);
	const [showEditAlert, setShowEditAlert] = useState(false);
	const [showUnlinkAlert, setShowUnlinkAlert] = useState(false);
	const [currentTitle, setCurrentTitle] = useState(project.title ?? '');
	const [currentDescription, setCurrentDescription] = useState(project.description || '');
	const [isDropdownOpen, setIsDropdownOpen] = useState(false);
	const { mutate: globalMutate } = useSWRConfig();

	const deleteProject = async () => {
		try {
			const response = await unwrap(api.DELETE("/api/projects/{project_id}", {
				params: { path: { project_id: project.id } },
			}));
			if (response) {
				setShowDeleteAlert(false);
				void globalMutate(PROJECTS_LIST_KEY);
				onProjectUpdate?.();
			} else {
				toast.error('Failed to delete project. Please try again.');
			}
		} catch (error) {
			console.error('An error occurred while deleting the project:', error);
			toast.error('An unexpected error occurred. Please try again.');
		}
	};

	const handleUpdateProject = async () => {
		try {
			const response = await unwrap(api.PATCH("/api/projects/{project_id}", {
				params: { path: { project_id: project.id } },
				body: {
					title: currentTitle,
					description: currentDescription,
				},
			}));
			if (response) {
				setShowEditAlert(false);
				void globalMutate(PROJECTS_LIST_KEY);
				onProjectUpdate?.();
			} else {
				toast.error('Failed to update project. Please try again.');
			}
		} catch (error) {
			console.error('An error occurred while updating the project:', error);
			toast.error('An unexpected error occurred. Please try again.');
		}
	};

	const handleEditClick = () => {
		setCurrentTitle(project.title ?? '');
		setCurrentDescription(project.description || '');
		setShowEditAlert(true);
		setIsDropdownOpen(false);
	};

	const handleDeleteClick = () => {
		setShowDeleteAlert(true);
		setIsDropdownOpen(false);
	};

	const handleCardClick = (e: React.MouseEvent) => {
		// Prevent navigation if dropdown is open or if clicking on dropdown area
		if (isDropdownOpen) {
			e.preventDefault();
		}
	};

	if (compact) {
		const updatedAt = project.updated_at
			? formatDate(project.updated_at)
			: null;

		return (
			<>
			<AlertDialog open={showUnlinkAlert} onOpenChange={setShowUnlinkAlert}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Are you sure you want to unlink this paper?</AlertDialogTitle>
						<AlertDialogDescription>
							This action will remove this paper from the project. You can add it back later.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction onClick={onUnlink}>Unlink</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
			<Link
				href={`/projects/${project.id}`}
				className="group flex items-center gap-3 rounded-xl border border-border/60 bg-card p-3.5 transition-[transform,box-shadow,border-color] duration-200 ease-out-soft hover:-translate-y-0.5 hover:border-border hover:shadow-md focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-brand/30 motion-safe:active:translate-y-0 motion-safe:active:scale-[0.99] sm:gap-4 sm:p-4"
			>
				<div className="flex-1 min-w-0">
					<h3 className="truncate font-medium transition-colors group-hover:text-brand">
						{project.title}
					</h3>
					<div className="flex items-center gap-3 text-sm text-muted-foreground mt-0.5">
						{project.num_papers !== undefined && (
							<span className="flex items-center gap-1">
								<FileText className="h-3.5 w-3.5" />
								{project.num_papers} {project.num_papers === 1 ? "paper" : "papers"}
							</span>
						)}
					</div>
				</div>

				{updatedAt && (
					<span className="hidden text-xs text-muted-foreground tabular-nums sm:block">
						{updatedAt}
					</span>
				)}

				<ArrowRight className="h-4 w-4 flex-shrink-0 text-muted-foreground opacity-0 transition-[opacity,transform] duration-200 ease-out-soft group-hover:translate-x-0.5 group-hover:opacity-100" />
				{onUnlink && (
					<Button
						variant="ghost"
						size="icon"
						onClick={(e) => {
							e.preventDefault();
							setShowUnlinkAlert(true);
						}}
						aria-label="Unlink"
						className="h-8 w-8 text-muted-foreground hover:text-foreground flex-shrink-0"
					>
						<X className="h-4 w-4" />
					</Button>
				)}
			</Link>
			</>
		);
	}

	return (
		<div className="group relative h-full transition-transform duration-200 ease-out-soft hover:-translate-y-0.5 has-[a:active]:translate-y-0 has-[a:active]:scale-[0.99]">
			<Link
				href={`/projects/${project.id}`}
				className="block h-full rounded-xl focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-brand/30"
				onClick={handleCardClick}
			>
				<Card className="h-full min-h-40 gap-3 border-border/60 py-5 shadow-none transition-[box-shadow,border-color] duration-200 ease-out-soft group-hover:border-border group-hover:shadow-md">
					<CardHeader className="gap-1.5 px-5 pr-12">
						<CardTitle className="line-clamp-2 text-base font-semibold leading-snug text-foreground">
							{project.title}
						</CardTitle>
						{project.description && (
							<CardDescription className="line-clamp-2 text-sm leading-relaxed text-muted-foreground">
								{project.description}
							</CardDescription>
						)}
					</CardHeader>
					<CardFooter className="mt-auto px-5">
						<div className="flex w-full items-center justify-between">
							<div className="flex items-center gap-3 text-xs text-muted-foreground tabular-nums">
								<span className="flex items-center gap-1">
									<FileText className="h-3.5 w-3.5" />
									{project.num_papers ?? 0} {project.num_papers === 1 ? "paper" : "papers"}
								</span>
								<span aria-hidden>·</span>
								<span>Updated {formatDate(project.updated_at)}</span>
							</div>
							<ArrowRight className="h-4 w-4 text-muted-foreground opacity-0 transition-[opacity,transform] duration-200 ease-out-soft group-hover:translate-x-0.5 group-hover:opacity-100" />
						</div>
					</CardFooter>
				</Card>
			</Link>
			{/* On hover-capable devices the menu shows on hover; on touch it's always there. */}
			{onUnlink ? (
				<div className="absolute top-3 right-3 z-20 transition-opacity duration-150 pointer-fine:opacity-0 pointer-fine:group-hover:opacity-100 focus-within:opacity-100">
					<Button
						variant="ghost"
						size="icon"
						aria-label="Unlink"
						onClick={(e) => {
							e.preventDefault();
							setShowUnlinkAlert(true);
						}}
						className="h-8 w-8 text-muted-foreground hover:text-foreground"
					>
						<X className="h-4 w-4" />
					</Button>
				</div>
			) : (
				<div className="absolute top-3 right-3 z-20 transition-opacity duration-150 pointer-fine:opacity-0 pointer-fine:group-hover:opacity-100 focus-within:opacity-100 has-[[data-state=open]]:opacity-100">
					<DropdownMenu open={isDropdownOpen} onOpenChange={setIsDropdownOpen}>
						<DropdownMenuTrigger asChild>
							<Button
								variant="ghost"
								size="icon"
								aria-label="Project actions"
								className="h-8 w-8 text-muted-foreground hover:text-foreground"
							>
								<MoreHorizontal className="h-4 w-4" />
							</Button>
						</DropdownMenuTrigger>
						<DropdownMenuContent align="end" className="w-32">
							<DropdownMenuItem
								onClick={handleEditClick}
								className="cursor-pointer"
							>
								Edit
							</DropdownMenuItem>
							<DropdownMenuItem
								onClick={handleDeleteClick}
								className="cursor-pointer text-destructive focus:text-destructive"
							>
								Delete
							</DropdownMenuItem>
						</DropdownMenuContent>
					</DropdownMenu>
				</div>
			)}

			<AlertDialog open={showDeleteAlert} onOpenChange={setShowDeleteAlert}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Are you absolutely sure?</AlertDialogTitle>
						<AlertDialogDescription>
							This action cannot be undone. This will permanently delete your project and remove your data from our servers.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction onClick={deleteProject}>Continue</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			<AlertDialog open={showEditAlert} onOpenChange={(isOpen) => {
				setShowEditAlert(isOpen);
				if (!isOpen) {
					setCurrentTitle(project.title ?? '');
					setCurrentDescription(project.description || '');
				}
			}}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Edit Project</AlertDialogTitle>
						<AlertDialogDescription>
							Update the title and description for your project.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<div className="grid gap-4 py-4">
						<div className="grid gap-2 sm:grid-cols-4 sm:items-center sm:gap-4">
							<Label htmlFor="title" className="sm:justify-end">
								Title
							</Label>
							<Input
								id="title"
								value={currentTitle}
								onChange={(e) => setCurrentTitle(e.target.value)}
								className="sm:col-span-3"
							/>
						</div>
						<div className="grid gap-2 sm:grid-cols-4 sm:items-center sm:gap-4">
							<Label htmlFor="description" className="sm:justify-end">
								Description
							</Label>
							<Input
								id="description"
								value={currentDescription}
								onChange={(e) => setCurrentDescription(e.target.value)}
								className="sm:col-span-3"
							/>
						</div>
					</div>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction onClick={handleUpdateProject}>Save</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			<AlertDialog open={showUnlinkAlert} onOpenChange={setShowUnlinkAlert}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Are you sure you want to unlink this paper?</AlertDialogTitle>
						<AlertDialogDescription>
							This action will remove this paper from the project. You can add it back later.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction onClick={onUnlink}>Unlink</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
