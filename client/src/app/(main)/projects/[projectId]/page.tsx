"use client";

import { AlertCircle, ArrowLeft, ArrowRight, BookOpen, Info, Library, Loader2, Pencil, PlusCircle, Search, Sparkles, UploadCloud } from "lucide-react";
import { useEffect, useState, useCallback, useMemo } from "react";
import { useParams, useRouter } from "next/navigation";
import { fetchFromApi } from "@/lib/api";
import { PdfDropzone } from "@/components/PdfDropzone";
import PaperCard from "@/components/PaperCard";
import PdfUploadTracker from "@/components/PdfUploadTracker";
import { CitePaperButton } from "@/components/CitePaperButton";
import { MinimalJob } from "@/lib/schema";
import { uploadFromUrlWithFallbackForProject } from "@/lib/uploadUtils";
import {
	Sheet,
	SheetContent,
	SheetHeader,
	SheetTitle,
	SheetTrigger,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import AddFromLibrary from "@/components/AddFromLibrary";
import {
	Dialog,
	DialogHeader,
	DialogContent,
	DialogTitle,
	DialogTrigger,
	DialogDescription
} from "@/components/ui/dialog";
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
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import Link from "next/link";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import {
	Breadcrumb,
	BreadcrumbItem,
	BreadcrumbLink,
	BreadcrumbList,
	BreadcrumbPage,
	BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { useSubscription, isPaperUploadAtLimit } from "@/hooks/useSubscription";
import { useProject, useProjectPapers } from "@/hooks/useProjects";
import { toast } from "sonner";
import ProjectPageSkeleton from "@/components/ProjectPageSkeleton";
import { PaperListSkeleton } from "@/components/PaperListSkeleton";

// Client-side paper limits per project
const PROJECT_PAPER_WARNING_LIMIT = 75;
const PROJECT_PAPER_HARD_LIMIT = 100;

export default function ProjectPage() {
	const params = useParams();
	const router = useRouter();
	const projectId = params.projectId as string;
	const { project, isLoading, error: projectError, refetch: refetchProject } = useProject(projectId);
	const { papers, isLoading: isPapersLoading, refetch: refetchPapers } = useProjectPapers(projectId);
	const [error, setError] = useState<string | null>(null);
	const [uploadError, setUploadError] = useState<string | null>(null);
	const [initialJobs, setInitialJobs] = useState<MinimalJob[]>([]);
	const [isUrlDialogOpen, setIsUrlDialogOpen] = useState(false);
	const [pdfUrl, setPdfUrl] = useState("");
	const [isUploading, setIsUploading] = useState(false);
	const [showEditAlert, setShowEditAlert] = useState(false);
	const [currentTitle, setCurrentTitle] = useState("");
	const [currentDescription, setCurrentDescription] = useState("");
	const [isAddPapersSheetOpen, setIsAddPapersSheetOpen] = useState(false);
	const [addPapersView, setAddPapersView] = useState<'initial' | 'upload' | 'library'>('initial');
	const [isUploadDialogOpen, setIsUploadDialogOpen] = useState(false);
	const [showAllPapers, setShowAllPapers] = useState(false);
	const [paperSearchQuery, setPaperSearchQuery] = useState("");
	const [paperSortBy, setPaperSortBy] = useState<"date_added" | "publish_date" | "title">("date_added");
	const { subscription } = useSubscription();

	// Paper limit checks
	const currentPaperCount = papers?.length || 0;
	const isAtPaperWarningLimit = currentPaperCount >= PROJECT_PAPER_WARNING_LIMIT;
	const isAtPaperHardLimit = currentPaperCount >= PROJECT_PAPER_HARD_LIMIT;
	const remainingPaperSlots = Math.max(0, PROJECT_PAPER_HARD_LIMIT - currentPaperCount);

	const filteredAndSortedPapers = useMemo(() => {
		if (!papers) return [];
		let result = [...papers];
		if (paperSearchQuery.trim()) {
			const q = paperSearchQuery.toLowerCase();
			result = result.filter(p =>
				(p.title?.toLowerCase().includes(q)) ||
				(p.authors?.some(a => a.toLowerCase().includes(q))) ||
				(p.keywords?.some(k => k.toLowerCase().includes(q)))
			);
		}
		result.sort((a, b) => {
			if (paperSortBy === "title") {
				return (a.title || "").localeCompare(b.title || "");
			} else if (paperSortBy === "publish_date") {
				return (b.publish_date || "").localeCompare(a.publish_date || "");
			} else {
				return (b.created_at || "").localeCompare(a.created_at || "");
			}
		});
		return result;
	}, [papers, paperSearchQuery, paperSortBy]);

	const handleFileSelect = async (files: File[]) => {
		if (isAtPaperHardLimit) {
			toast.error(`This project has reached the maximum of ${PROJECT_PAPER_HARD_LIMIT} papers. Remove some papers before adding more.`);
			return;
		}
		if (isPaperUploadAtLimit(subscription)) {
			setUploadError("You have reached your paper upload limit. Please upgrade your plan to upload more papers.");
			return;
		}
		if (files.length > remainingPaperSlots) {
			toast.error(`You can only add ${remainingPaperSlots} more paper${remainingPaperSlots === 1 ? '' : 's'} to this project (limit: ${PROJECT_PAPER_HARD_LIMIT}).`);
			return;
		}
		setUploadError(null);
		const newJobs: MinimalJob[] = [];
		if (files.length > 0) {
			setIsAddPapersSheetOpen(false);
			setIsUploadDialogOpen(false);
		}
		for (const file of files) {
			const formData = new FormData();
			formData.append("file", file);

			try {
				const response = await fetchFromApi(`/api/paper/upload?project_id=${projectId}`, {
					method: "POST",
					body: formData,
				});
				newJobs.push({ jobId: response.job_id, fileName: file.name });
			} catch (err) {
				setUploadError(`Failed to upload file: ${file.name}. Please try again.`);
				console.error(err);
			}
		}
		setInitialJobs((prevJobs) => [...prevJobs, ...newJobs]);
	};

	const handleUploadComplete = useCallback(async () => {
		refetchPapers();
	}, [refetchPapers]);

	const handlePdfUrl = async (url: string) => {
		if (isAtPaperHardLimit) {
			toast.error(`This project has reached the maximum of ${PROJECT_PAPER_HARD_LIMIT} papers. Remove some papers before adding more.`);
			setIsUrlDialogOpen(false);
			return;
		}
		if (isPaperUploadAtLimit(subscription)) {
			setUploadError("You have reached your paper upload limit. Please upgrade your plan to upload more papers.");
			setIsUrlDialogOpen(false);
			return;
		}
		setIsUploading(true);
		try {
			const job = await uploadFromUrlWithFallbackForProject(url, projectId);
			setInitialJobs((prevJobs) => [...prevJobs, { jobId: job.jobId, fileName: job.fileName }]);
			// Close sheet and dialogs on success
			setIsAddPapersSheetOpen(false);
			setIsUploadDialogOpen(false);
		} catch (serverError) {
			console.error('Both client and server-side fetches failed:', serverError);
			setUploadError(`Failed to upload file from url: ${url}. Please try again.`);
		} finally {
			setIsUploading(false);
			setIsUrlDialogOpen(false);
		}
	};

	const handleLinkClick = () => {
		setIsUrlDialogOpen(true);
	};

	const handleDialogConfirm = async () => {
		if (pdfUrl) {
			await handlePdfUrl(pdfUrl);
		}
		setIsUrlDialogOpen(false);
		setPdfUrl("");
	};

	const handleUpdateProject = async () => {
		if (!project) return;
		try {
			const response = await fetchFromApi(`/api/projects/${project.id}`, {
				method: 'PATCH',
				headers: {
					'Content-Type': 'application/json',
				},
				body: JSON.stringify({
					title: currentTitle,
					description: currentDescription,
				}),
			});
			if (response) {
				refetchProject();
				setShowEditAlert(false);
			} else {
				console.error('Failed to update project');
			}
		} catch (error) {
			console.error('An error occurred while updating the project:', error);
		}
	};

	const handleEditClick = () => {
		if (!project) return;
		setCurrentTitle(project.title);
		setCurrentDescription(project.description || '');
		setShowEditAlert(true);
	};

	if (isLoading) {
		return <ProjectPageSkeleton />;
	}

	if (projectError || error) {
		return <div className="container mx-auto p-4 text-red-500">{projectError?.message || error}</div>;
	}

	if (!project) {
		return <div className="container mx-auto p-4">Project not found.</div>;
	}

	// Show full skeleton only on initial load when we have no data yet
	if (isPapersLoading && !papers?.length) {
		return <ProjectPageSkeleton />;
	}

	const isEmpty = !isPapersLoading && (!papers || papers.length === 0);

	if (isEmpty) {
		return (
			<div className="container mx-auto p-4">

				<div className="group relative">
					<div className="flex items-center">
						<h1 className="text-3xl font-bold text-primary rounded-lg px-0">{project.title}</h1>
						<Button
							variant="ghost"
							size="icon"
							className="opacity-0 group-hover:opacity-100 ml-2"
							onClick={handleEditClick}
						>
							<Pencil className="h-4 w-4" />
						</Button>
					</div>
					{project.description ? (
						<p className="text-lg text-secondary-foreground mb-8">{project.description}</p>
					) : (
						<button
							className="text-lg text-muted-foreground/60 mb-8 cursor-pointer hover:text-muted-foreground transition-colors bg-transparent border-none p-0 text-left"
							onClick={handleEditClick}
						>
							Add a description...
						</button>
					)}
				</div>

				<PdfUploadTracker initialJobs={initialJobs} onComplete={handleUploadComplete} />

				<div className="flex flex-col items-center justify-center py-12 max-w-lg mx-auto text-center">
					<div className="p-4 bg-blue-100 dark:bg-blue-900/30 rounded-full w-16 h-16 mb-4 flex items-center justify-center">
						<BookOpen className="w-8 h-8 text-blue-500" />
					</div>
					<h2 className="text-2xl font-bold mb-2">Get Started with Your Project</h2>
					<p className="text-muted-foreground mb-8">Add research papers to your project, then ask questions and generate insights.</p>

					<div className="grid grid-cols-1 sm:grid-cols-2 gap-4 w-full mb-8">
						<button
							onClick={() => setIsUploadDialogOpen(true)}
							className="flex flex-col items-center justify-center p-6 border-2 border-dashed rounded-lg hover:bg-accent transition-colors group"
						>
							<UploadCloud className="w-10 h-10 text-muted-foreground group-hover:text-blue-500 mb-3 transition-colors" />
							<h3 className="font-semibold group-hover:text-blue-600 transition-colors">Upload Papers</h3>
							<p className="text-sm text-muted-foreground mt-1">Upload PDFs from your computer</p>
						</button>
						<button
							onClick={() => setIsAddPapersSheetOpen(true)}
							className="flex flex-col items-center justify-center p-6 border-2 border-dashed rounded-lg hover:bg-accent transition-colors group"
						>
							<Library className="w-10 h-10 text-muted-foreground group-hover:text-blue-500 mb-3 transition-colors" />
							<h3 className="font-semibold group-hover:text-blue-600 transition-colors">Add from Library</h3>
							<p className="text-sm text-muted-foreground mt-1">Choose from your existing papers</p>
						</button>
					</div>

					<div className="flex items-center gap-3 text-sm text-muted-foreground">
						<div className="flex items-center gap-1.5">
							<span className="flex items-center justify-center w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900/30 text-blue-600 text-xs font-medium">1</span>
							Add papers
						</div>
						<ArrowRight className="h-3 w-3" />
						<div className="flex items-center gap-1.5">
							<span className="flex items-center justify-center w-5 h-5 rounded-full bg-muted text-muted-foreground text-xs font-medium">2</span>
							Ask questions
						</div>
						<ArrowRight className="h-3 w-3" />
						<div className="flex items-center gap-1.5">
							<span className="flex items-center justify-center w-5 h-5 rounded-full bg-muted text-muted-foreground text-xs font-medium">3</span>
							Generate insights
						</div>
					</div>
				</div>

				<div className="mt-4">
					<AddFromLibrary projectId={projectId} onPapersAdded={refetchPapers} projectPaperIds={papers.map(p => p.id)} onUploadClick={() => setIsUploadDialogOpen(true)} remainingPaperSlots={remainingPaperSlots} paperHardLimit={PROJECT_PAPER_HARD_LIMIT} />
				</div>

				<Dialog open={isUploadDialogOpen} onOpenChange={setIsUploadDialogOpen}>
					<DialogContent>
						<DialogHeader>
							<DialogTitle>Upload New Papers</DialogTitle>
							<DialogDescription>You can upload any additional papers to your library here. They will automatically be added to the project.</DialogDescription>
						</DialogHeader>
						<PdfDropzone onFileSelect={handleFileSelect} onUrlClick={handleLinkClick} disabled={isPaperUploadAtLimit(subscription) || isAtPaperHardLimit} />
						{isPaperUploadAtLimit(subscription) && (
							<Alert variant="destructive" className="mt-4">
								<AlertCircle className="h-4 w-4" />
								<AlertTitle>Upload Limit Reached</AlertTitle>
								<AlertDescription>
									You have reached your paper upload limit. Please{" "}
									<Link href="/pricing" className="font-bold underline">
										upgrade your plan
									</Link>{" "}
									to upload more papers.
								</AlertDescription>
							</Alert>
						)}
						{uploadError && <p className="text-red-500 mt-4">{uploadError}</p>}
					</DialogContent>
				</Dialog>
				<Dialog open={isUrlDialogOpen} onOpenChange={setIsUrlDialogOpen}>
					<DialogContent>
						<DialogHeader>
							<DialogTitle>Import PDF from URL</DialogTitle>
							<DialogDescription>
								Enter the public URL of the PDF you want to upload.
							</DialogDescription>
						</DialogHeader>
						<Input
							type="url"
							placeholder="https://arxiv.org/pdf/1706.03762v7"
							value={pdfUrl}
							onChange={(e) => setPdfUrl(e.target.value)}
							className="mt-4"
						/>
						<div className="flex justify-end gap-2 mt-4">
							<Button variant="secondary" onClick={() => setIsUrlDialogOpen(false)}>
								Cancel
							</Button>
							<Button onClick={handleDialogConfirm} disabled={!pdfUrl || isUploading}>
								{isUploading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
								Submit
							</Button>
						</div>
					</DialogContent>
				</Dialog>
				<AlertDialog open={showEditAlert} onOpenChange={setShowEditAlert}>
					<AlertDialogContent>
						<AlertDialogHeader>
							<AlertDialogTitle>Edit Project</AlertDialogTitle>
							<AlertDialogDescription>
								Update the title and description for your project.
							</AlertDialogDescription>
						</AlertDialogHeader>
						<div className="grid gap-4 py-4">
							<div className="grid grid-cols-4 items-center gap-4">
								<Label htmlFor="title" className="text-right">
									Title
								</Label>
								<Input
									id="title"
									value={currentTitle}
									onChange={(e) => setCurrentTitle(e.target.value)}
									className="col-span-3"
								/>
							</div>
							<div className="grid grid-cols-4 items-center gap-4">
								<Label htmlFor="description" className="text-right">
									Description
								</Label>
								<Textarea
									id="description"
									value={currentDescription}
									onChange={(e) => setCurrentDescription(e.target.value)}
									className="col-span-3"
								/>
							</div>
						</div>
						<AlertDialogFooter>
							<AlertDialogCancel>Cancel</AlertDialogCancel>
							<AlertDialogAction onClick={handleUpdateProject}>Save</AlertDialogAction>
						</AlertDialogFooter>
					</AlertDialogContent>
				</AlertDialog>
			</div>
		)
	}

	return (
		<div className="container mx-auto p-4">
			<Breadcrumb className="mb-4">
				<BreadcrumbList>
					<BreadcrumbItem>
						<BreadcrumbLink href="/projects">Projects</BreadcrumbLink>
					</BreadcrumbItem>
					<BreadcrumbSeparator />
					<BreadcrumbItem>
						<BreadcrumbPage>{project.title}</BreadcrumbPage>
					</BreadcrumbItem>
				</BreadcrumbList>
			</Breadcrumb>
			<PdfUploadTracker initialJobs={initialJobs} onComplete={handleUploadComplete} />


			<div className="group relative">
				<div className="flex items-start justify-between gap-4">
					<div className="flex-1">
						<div className="flex items-center">
							<h1 className="text-3xl font-bold text-primary p-2 rounded-lg px-0">{project.title}</h1>
							<Button
								variant="ghost"
								size="icon"
								className="opacity-0 group-hover:opacity-100 ml-2"
								onClick={handleEditClick}
							>
								<Pencil className="h-4 w-4" />
							</Button>
						</div>
						{project.description ? (
							<p className="text-lg text-secondary-foreground mb-6">{project.description}</p>
						) : (
							<button
								className="text-lg text-muted-foreground/60 mb-6 cursor-pointer hover:text-muted-foreground transition-colors bg-transparent border-none p-0 text-left"
								onClick={handleEditClick}
							>
								Add a description...
							</button>
						)}
					</div>
				</div>
			</div>


			<div className="flex flex-col lg:flex-row gap-6 -mx-4">
				{/* Papers */}
				<div className="w-full px-4">
					<div className="flex justify-between items-center mb-4">
						<h2 className="text-2xl font-bold">Papers</h2>
						<div className="flex gap-2">
							{papers.length > 0 && (
								<CitePaperButton paper={papers} minimalist={true} />
							)}
							<Sheet open={isAddPapersSheetOpen} onOpenChange={(isOpen) => {
								if (isOpen && isAtPaperHardLimit) {
									toast.error(`This project has reached the maximum of ${PROJECT_PAPER_HARD_LIMIT} papers. Remove some papers before adding more.`);
									return;
								}
								setIsAddPapersSheetOpen(isOpen);
								if (!isOpen) {
									setAddPapersView('initial');
								}
							}}>
								{isAtPaperHardLimit ? (
									<Tooltip>
										<TooltipTrigger asChild>
											<span tabIndex={0}>
												<Button variant="outline" disabled className="pointer-events-none">
													<PlusCircle className="mr-2 h-4 w-4" />
													Add
												</Button>
											</span>
										</TooltipTrigger>
										<TooltipContent className="max-w-xs">
											<p>Paper limit reached ({PROJECT_PAPER_HARD_LIMIT} max). Remove papers to add more, or contact <a href="mailto:saba@openpaper.ai" className="underline">saba@openpaper.ai</a> for higher limits.</p>
										</TooltipContent>
									</Tooltip>
								) : (
									<SheetTrigger asChild>
										<Button variant="outline">
											<PlusCircle className="mr-2 h-4 w-4" />
											Add
										</Button>
									</SheetTrigger>
								)}
								<SheetContent className="sm:max-w-[90vw]! w-[90vw] overflow-y-auto">
									<SheetHeader className="px-6">
										<SheetTitle>Add Papers to Project</SheetTitle>
									</SheetHeader>
									<div className="mt-0 px-6">
										{/* Paper limit info */}
										<div className={`flex items-start gap-2 p-3 rounded-lg mt-4 ${isAtPaperHardLimit ? 'bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-800' : isAtPaperWarningLimit ? 'bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-800' : 'bg-blue-50 dark:bg-blue-950 border border-blue-200 dark:border-blue-800'}`}>
											<Info className={`h-4 w-4 mt-0.5 flex-shrink-0 ${isAtPaperHardLimit ? 'text-red-500' : isAtPaperWarningLimit ? 'text-amber-500' : 'text-blue-500'}`} />
											<div className="text-sm">
												<p className={`font-medium ${isAtPaperHardLimit ? 'text-red-700 dark:text-red-300' : isAtPaperWarningLimit ? 'text-amber-700 dark:text-amber-300' : 'text-blue-700 dark:text-blue-300'}`}>
													{currentPaperCount} / {PROJECT_PAPER_HARD_LIMIT} papers in this project
												</p>
												{isAtPaperHardLimit ? (
													<p className="text-red-600 dark:text-red-400 mt-1">
														You&apos;ve reached the maximum. Remove papers to add more.
													</p>
												) : isAtPaperWarningLimit ? (
													<p className="text-amber-600 dark:text-amber-400 mt-1">
														Large paper counts may impact response quality. For higher limits, contact <a href="mailto:saba@openpaper.ai" className="underline font-medium">saba@openpaper.ai</a>
													</p>
												) : (
													<p className="text-blue-600 dark:text-blue-400 mt-1">
														You can add {remainingPaperSlots} more paper{remainingPaperSlots === 1 ? '' : 's'}.
													</p>
												)}
											</div>
										</div>

										{addPapersView === 'initial' && (
											<div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-6">
												<button
													onClick={() => setAddPapersView('upload')}
													className="flex flex-col items-center justify-center p-6 border-2 border-dashed rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors group"
												>
													<div className="relative">
														<UploadCloud className="w-12 h-12 text-gray-400 group-hover:text-blue-500 mb-4 transition-colors" />
														<div className="absolute -top-1 -right-1 w-5 h-5 bg-blue-100 dark:bg-blue-900 rounded-full flex items-center justify-center">
															<span className="text-xs font-medium text-blue-600 dark:text-blue-300"><PlusCircle className="h-4 w-4" /></span>
														</div>
													</div>
													<h3 className="text-lg font-semibold group-hover:text-blue-600 transition-colors">Upload New Papers</h3>
													<p className="text-sm text-gray-500 text-center mt-1">
														Upload PDFs from your computer or URL
													</p>
													<p className="text-xs mt-2 font-medium">
														Drag & drop or browse →
													</p>
												</button>
												<button
													onClick={() => setAddPapersView('library')}
													className="flex flex-col items-center justify-center p-6 border-2 border-dashed rounded-lg hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors group"
												>
													<div className="relative">
														<Library className="w-12 h-12 text-gray-400 group-hover:text-blue-500 mb-4 transition-colors" />
														<div className="absolute -top-1 -right-1 w-5 h-5 bg-blue-100 dark:bg-blue-900 rounded-full flex items-center justify-center">
															<span className="text-xs font-medium text-blue-600 dark:text-blue-300"><BookOpen className="h-4 w-4" /></span>
														</div>
													</div>
													<h3 className="text-lg font-semibold group-hover:text-blue-600 transition-colors">Add from Library</h3>
													<p className="text-sm text-gray-500 text-center mt-1">
														Choose from papers already in your library
													</p>
													<p className="text-xs mt-2 font-medium">
														Browse existing papers →
													</p>
												</button>
											</div>
										)}

										{addPapersView === 'upload' && (
											<div>
												<Button variant="ghost" onClick={() => setAddPapersView('initial')} className="mb-4">
													<ArrowLeft className="mr-2 h-4 w-4" />
													Back
												</Button>
												<h3 className="text-lg font-semibold mb-2">Upload New Papers</h3>
												<p className="text-sm text-gray-500 mb-4">Upload papers to your library. They will be automatically added to this project.</p>
												<PdfDropzone onFileSelect={handleFileSelect} onUrlClick={handleLinkClick} disabled={isPaperUploadAtLimit(subscription) || isAtPaperHardLimit} />
												{isPaperUploadAtLimit(subscription) && (
													<Alert variant="destructive" className="mt-4">
														<AlertCircle className="h-4 w-4" />
														<AlertTitle>Upload Limit Reached</AlertTitle>
														<AlertDescription>
															You have reached your paper upload limit. Please{" "}
															<Link href="/pricing" className="font-bold underline">
																upgrade your plan
															</Link>{" "}
															to upload more papers.
														</AlertDescription>
													</Alert>
												)}
												{uploadError && <p className="text-red-500 mt-4">{uploadError}</p>}
											</div>
										)}

										{addPapersView === 'library' && (
											<div>
												<Button variant="ghost" onClick={() => setAddPapersView('initial')} className="mb-4">
													<ArrowLeft className="mr-2 h-4 w-4" />
													Back
												</Button>
												<h3 className="text-lg font-semibold mb-2">Add from Library</h3>
												<AddFromLibrary projectId={projectId} onPapersAdded={refetchPapers} projectPaperIds={papers.map(p => p.id)} onUploadClick={() => setIsUploadDialogOpen(true)} remainingPaperSlots={remainingPaperSlots} paperHardLimit={PROJECT_PAPER_HARD_LIMIT} />
											</div>
										)}
									</div>
								</SheetContent>
							</Sheet>
						</div>
					</div>

					{papers && papers.length > 3 && (
						<div className="flex gap-2 mb-4">
							<div className="relative flex-1">
								<Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
								<Input
									placeholder="Search papers..."
									value={paperSearchQuery}
									onChange={(e) => setPaperSearchQuery(e.target.value)}
									className="pl-9 h-9"
								/>
							</div>
							<Select value={paperSortBy} onValueChange={(v) => setPaperSortBy(v as "date_added" | "publish_date" | "title")}>
								<SelectTrigger size="sm">
									<SelectValue />
								</SelectTrigger>
								<SelectContent>
									<SelectItem value="date_added">Date Added</SelectItem>
									<SelectItem value="publish_date">Publish Date</SelectItem>
									<SelectItem value="title">Title</SelectItem>
								</SelectContent>
							</Select>
						</div>
					)}

					{isPapersLoading ? (
						<PaperListSkeleton count={3} />
					) : papers && papers.length > 0 ? (
						<div className="flex flex-col gap-6">
							<div>
								<div className="grid grid-cols-1 gap-4">
									{filteredAndSortedPapers.slice(0, showAllPapers ? filteredAndSortedPapers.length : 3).map((paper) => (
										<div key={paper.id}>
											<PaperCard paper={paper} minimalist={true} projectId={projectId} onUnlink={refetchPapers} />
										</div>
									))}
								</div>
								{filteredAndSortedPapers.length > 3 && !showAllPapers && (
									<div className="mt-4 text-left">
										<Button variant="ghost" className="p-0 h-auto" onClick={() => setShowAllPapers(true)}>
											Show All {filteredAndSortedPapers.length}
										</Button>
									</div>
								)}
							</div>
							{filteredAndSortedPapers.length === 0 && paperSearchQuery.trim() && (
								<div className="text-center py-8 text-muted-foreground">
									<Search className="h-8 w-8 mx-auto mb-2 opacity-50" />
									<p className="text-sm">No papers matching &ldquo;{paperSearchQuery}&rdquo;</p>
								</div>
							)}
						</div>
					) : (
						<div className="text-center p-8 border-dashed border-2 border-gray-300 dark:border-gray-700 rounded-xl bg-gray-50 dark:bg-gray-800/50">
							<div className="p-4 bg-gray-100 dark:bg-gray-700/50 rounded-full w-16 h-16 mx-auto mb-4 flex items-center justify-center">
								<Sparkles className="w-8 h-8 text-gray-400 dark:text-gray-500" />
							</div>
							<h3 className="text-lg font-semibold text-gray-600 dark:text-gray-300 mb-2">No Papers Yet</h3>
							<p className="text-gray-500 dark:text-gray-400 mb-4">Add papers to start analyzing and discussing them.</p>
							<Dialog>
								<DialogTrigger asChild>
									<Button variant="outline">Upload Papers</Button>
								</DialogTrigger>
								<DialogContent>
									<DialogHeader>
										<DialogTitle>Upload New Papers</DialogTitle>
										<DialogDescription>You can upload any additional papers to your library here. They will automatically be added to the project.</DialogDescription>
									</DialogHeader>
									<PdfDropzone onFileSelect={handleFileSelect} onUrlClick={handleLinkClick} disabled={isPaperUploadAtLimit(subscription) || isAtPaperHardLimit} />
									{isPaperUploadAtLimit(subscription) && (
										<Alert variant="destructive" className="mt-4">
											<AlertCircle className="h-4 w-4" />
											<AlertTitle>Upload Limit Reached</AlertTitle>
											<AlertDescription>
												You have reached your paper upload limit. Please{" "}
												<Link href="/pricing" className="font-bold underline">
													upgrade your plan
												</Link>{" "}
												to upload more papers.
											</AlertDescription>
										</Alert>
									)}
									{uploadError && <p className="text-red-500 mt-4">{uploadError}</p>}
								</DialogContent>
							</Dialog>
						</div>
					)}

				</div>
			</div>

			<AlertDialog open={showEditAlert} onOpenChange={setShowEditAlert}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Edit Project</AlertDialogTitle>
						<AlertDialogDescription>
							Update the title and description for your project.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<div className="grid gap-4 py-4">
						<div className="grid grid-cols-4 items-center gap-4">
							<Label htmlFor="title" className="text-right">
								Title
							</Label>
							<Input
								id="title"
								value={currentTitle}
								onChange={(e) => setCurrentTitle(e.target.value)}
								className="col-span-3"
							/>
						</div>
						<div className="grid grid-cols-4 items-center gap-4">
							<Label htmlFor="description" className="text-right">
								Description
							</Label>
							<Textarea
								id="description"
								value={currentDescription}
								onChange={(e) => setCurrentDescription(e.target.value)}
								className="col-span-3"
							/>
						</div>
					</div>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction onClick={handleUpdateProject}>Save</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			<Dialog open={isUrlDialogOpen} onOpenChange={setIsUrlDialogOpen}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Import PDF from URL</DialogTitle>
						<DialogDescription>
							Enter the public URL of the PDF you want to upload.
						</DialogDescription>
					</DialogHeader>
					<Input
						type="url"
						placeholder="https://arxiv.org/pdf/1706.03762v7"
						value={pdfUrl}
						onChange={(e) => setPdfUrl(e.target.value)}
						className="mt-4"
					/>
					<div className="flex justify-end gap-2 mt-4">
						<Button variant="secondary" onClick={() => setIsUrlDialogOpen(false)}>
							Cancel
						</Button>
						<Button onClick={handleDialogConfirm} disabled={!pdfUrl || isUploading}>
							{isUploading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
							Submit
						</Button>
					</div>
				</DialogContent>
			</Dialog>


		</div>
	);
}
