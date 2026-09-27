"use client";

import { ArrowLeft, ArrowRight, BookOpen, ChevronLeft, Library, Loader2, Pencil, Plus, PlusCircle, Search, Sparkles, UploadCloud } from "lucide-react";
import Link from "next/link";
import { useState, useMemo } from "react";
import { useParams } from "next/navigation";
import { api, unwrap } from "@/lib/api/client";
import { PdfDropzone } from "@/components/PdfDropzone";
import PaperCard from "@/components/PaperCard";
import { CitePaperButton } from "@/components/CitePaperButton";
import { uploadFile, uploadFromUrlWithFallback } from "@/lib/uploadUtils";
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
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { PROJECTS_LIST_KEY, useProject, useProjectPapers } from "@/hooks/useProjects";
import { useSWRConfig } from "swr";
import ProjectPageSkeleton from "@/components/ProjectPageSkeleton";
import { PaperListSkeleton } from "@/components/PaperListSkeleton";

function ProjectHeader({ title, description, onEdit }: { title: string | null | undefined; description: string | null | undefined; onEdit: () => void }) {
	return (
		<div className="mb-6 sm:mb-8">
			<Link
				href="/projects"
				className="-ml-2 mb-2 inline-flex h-8 items-center gap-1 rounded-md px-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
			>
				<ChevronLeft className="h-4 w-4" />
				Projects
			</Link>
			<div className="group flex items-start gap-1">
				<h1 className="min-w-0 break-words text-2xl font-semibold tracking-tight sm:text-3xl">{title}</h1>
				<Button
					variant="ghost"
					size="icon"
					aria-label="Edit project"
					className="shrink-0 text-muted-foreground transition-opacity hover:text-foreground focus-visible:opacity-100 pointer-fine:opacity-0 pointer-fine:group-hover:opacity-100"
					onClick={onEdit}
				>
					<Pencil className="h-4 w-4" />
				</Button>
			</div>
			{description ? (
				<p className="mt-1 max-w-3xl text-base text-muted-foreground sm:text-lg">{description}</p>
			) : (
				<button
					className="mt-1 cursor-pointer border-none bg-transparent p-0 text-left text-base text-muted-foreground/60 transition-colors hover:text-muted-foreground sm:text-lg"
					onClick={onEdit}
				>
					Add a description...
				</button>
			)}
		</div>
	);
}

export default function ProjectPage() {
	const params = useParams();
	const projectId = params.projectId as string;
	const { project, isLoading, error: projectError, refetch: refetchProject } = useProject(projectId);
	const { mutate: globalMutate } = useSWRConfig();
	const { papers, isLoading: isPapersLoading, refetch: refetchPapers } = useProjectPapers(projectId);
	const [error] = useState<string | null>(null);
	const [uploadError, setUploadError] = useState<string | null>(null);
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

	// Uploaded papers are in the project (and readable) as soon as the
	// upload returns; their ingest finishes in the background.
	const handleFileSelect = async (files: File[]) => {
		setUploadError(null);
		if (files.length > 0) {
			setIsAddPapersSheetOpen(false);
			setIsUploadDialogOpen(false);
		}
		for (const file of files) {
			try {
				await uploadFile(file, projectId);
			} catch (err) {
				setUploadError(`Failed to upload file: ${file.name}. Please try again.`);
				console.error(err);
			}
		}
		refetchPapers();
	};

	const handlePdfUrl = async (url: string) => {
		setIsUploading(true);
		try {
			await uploadFromUrlWithFallback(url, projectId);
			refetchPapers();
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
			const response = await unwrap(api.PATCH("/api/projects/{project_id}", {
				params: { path: { project_id: project.id } },
				body: {
					title: currentTitle,
					description: currentDescription,
				},
			}));
			if (response) {
				refetchProject();
				void globalMutate(PROJECTS_LIST_KEY);
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
		setCurrentTitle(project.title ?? '');
		setCurrentDescription(project.description || '');
		setShowEditAlert(true);
	};

	if (isLoading) {
		return <ProjectPageSkeleton />;
	}

	// A failed revalidation keeps the cached project on screen.
	if ((projectError && !project) || error) {
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
			<div className="mx-auto w-full max-w-6xl px-4 py-5 sm:px-6 sm:py-8 lg:px-8">
				<ProjectHeader title={project.title} description={project.description} onEdit={handleEditClick} />

				<div className="mx-auto flex max-w-lg animate-rise-in flex-col items-center justify-center py-6 text-center sm:py-12">
					<div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-brand/10">
						<BookOpen className="h-7 w-7 text-brand" />
					</div>
					<h2 className="mb-2 text-xl font-semibold sm:text-2xl">Get started with your project</h2>
					<p className="text-muted-foreground mb-8">Add research papers to your project, then ask questions and generate insights.</p>

					<div className="grid grid-cols-1 sm:grid-cols-2 gap-4 w-full mb-8">
						<button
							onClick={() => setIsUploadDialogOpen(true)}
							className="group grid grid-cols-[auto_1fr] items-center gap-x-4 rounded-xl border-2 border-dashed p-4 text-left transition-[border-color,background-color,transform] duration-200 ease-out-soft hover:border-brand/40 hover:bg-brand/[0.04] focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-brand/30 motion-safe:active:scale-[0.98] sm:flex sm:flex-col sm:justify-center sm:p-6 sm:text-center"
						>
							<UploadCloud className="row-span-2 h-8 w-8 text-muted-foreground transition-colors group-hover:text-brand sm:mb-3 sm:h-9 sm:w-9" />
							<h3 className="font-semibold">Upload Papers</h3>
							<p className="text-sm text-muted-foreground sm:mt-1">Upload PDFs from your computer</p>
						</button>
						<button
							onClick={() => setIsAddPapersSheetOpen(true)}
							className="group grid grid-cols-[auto_1fr] items-center gap-x-4 rounded-xl border-2 border-dashed p-4 text-left transition-[border-color,background-color,transform] duration-200 ease-out-soft hover:border-brand/40 hover:bg-brand/[0.04] focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-brand/30 motion-safe:active:scale-[0.98] sm:flex sm:flex-col sm:justify-center sm:p-6 sm:text-center"
						>
							<Library className="row-span-2 h-8 w-8 text-muted-foreground transition-colors group-hover:text-brand sm:mb-3 sm:h-9 sm:w-9" />
							<h3 className="font-semibold">Add from Library</h3>
							<p className="text-sm text-muted-foreground sm:mt-1">Choose from your existing papers</p>
						</button>
					</div>

					<div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-2 text-sm text-muted-foreground">
						<div className="flex items-center gap-1.5">
							<span className="flex h-5 w-5 items-center justify-center rounded-full bg-brand/10 text-xs font-medium text-brand">1</span>
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
					<AddFromLibrary projectId={projectId} onPapersAdded={refetchPapers} projectPaperIds={papers.map(p => p.id)} onUploadClick={() => setIsUploadDialogOpen(true)} />
				</div>

				<Dialog open={isUploadDialogOpen} onOpenChange={setIsUploadDialogOpen}>
					<DialogContent>
						<DialogHeader>
							<DialogTitle>Upload New Papers</DialogTitle>
							<DialogDescription>You can upload any additional papers to your library here. They will automatically be added to the project.</DialogDescription>
						</DialogHeader>
						<PdfDropzone onFileSelect={handleFileSelect} onUrlClick={handleLinkClick} />
						{uploadError && <p className="mt-4 text-sm text-destructive">{uploadError}</p>}
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
								<Textarea
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
			</div>
		)
	}

	return (
		<div className="mx-auto w-full max-w-6xl px-4 py-5 sm:px-6 sm:py-8 lg:px-8">
			<ProjectHeader title={project.title} description={project.description} onEdit={handleEditClick} />

			<div>
				{/* Papers */}
				<div className="w-full">
					<div className="mb-4 flex items-center justify-between gap-3">
						<h2 className="flex items-baseline gap-2 text-lg font-semibold">
							Papers
							<span className="text-sm font-normal text-muted-foreground tabular-nums">{papers.length}</span>
						</h2>
						<div className="flex shrink-0 gap-2">
							{papers.length > 0 && (
								<CitePaperButton paper={papers} minimalist={true} />
							)}
							<Sheet open={isAddPapersSheetOpen} onOpenChange={(isOpen) => {
								setIsAddPapersSheetOpen(isOpen);
								if (!isOpen) {
									setAddPapersView('initial');
								}
							}}>
									<SheetTrigger asChild>
										<Button className="bg-brand text-brand-foreground hover:bg-brand/90">
											<Plus />
											Add
										</Button>
									</SheetTrigger>
								<SheetContent className="w-full overflow-y-auto sm:w-[90vw] sm:max-w-[90vw]!">
									<SheetHeader className="px-6">
										<SheetTitle>Add Papers to Project</SheetTitle>
									</SheetHeader>
									<div className="mt-0 px-6">
										{addPapersView === 'initial' && (
											<div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-6">
												<button
													onClick={() => setAddPapersView('upload')}
													className="group flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-6 transition-[border-color,background-color,transform] duration-200 ease-out-soft hover:border-brand/40 hover:bg-brand/[0.04] focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-brand/30 motion-safe:active:scale-[0.98]"
												>
													<div className="relative">
														<UploadCloud className="mb-4 h-12 w-12 text-muted-foreground transition-colors group-hover:text-brand" />
														<div className="absolute -top-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-brand/10">
															<span className="text-xs font-medium text-brand"><PlusCircle className="h-4 w-4" /></span>
														</div>
													</div>
													<h3 className="text-lg font-semibold">Upload New Papers</h3>
													<p className="mt-1 text-center text-sm text-muted-foreground">
														Upload PDFs from your computer or URL
													</p>
													<p className="text-xs mt-2 font-medium">
														Drag & drop or browse →
													</p>
												</button>
												<button
													onClick={() => setAddPapersView('library')}
													className="group flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-6 transition-[border-color,background-color,transform] duration-200 ease-out-soft hover:border-brand/40 hover:bg-brand/[0.04] focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-brand/30 motion-safe:active:scale-[0.98]"
												>
													<div className="relative">
														<Library className="mb-4 h-12 w-12 text-muted-foreground transition-colors group-hover:text-brand" />
														<div className="absolute -top-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-brand/10">
															<span className="text-xs font-medium text-brand"><BookOpen className="h-4 w-4" /></span>
														</div>
													</div>
													<h3 className="text-lg font-semibold">Add from Library</h3>
													<p className="mt-1 text-center text-sm text-muted-foreground">
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
												<p className="mb-4 text-sm text-muted-foreground">Upload papers to your library. They will be automatically added to this project.</p>
												<PdfDropzone onFileSelect={handleFileSelect} onUrlClick={handleLinkClick} />
												{uploadError && <p className="mt-4 text-sm text-destructive">{uploadError}</p>}
											</div>
										)}

										{addPapersView === 'library' && (
											<div>
												<Button variant="ghost" onClick={() => setAddPapersView('initial')} className="mb-4">
													<ArrowLeft className="mr-2 h-4 w-4" />
													Back
												</Button>
												<h3 className="text-lg font-semibold mb-2">Add from Library</h3>
												<AddFromLibrary projectId={projectId} onPapersAdded={refetchPapers} projectPaperIds={papers.map(p => p.id)} onUploadClick={() => setIsUploadDialogOpen(true)} />
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
								<Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
								<Input
									aria-label="Search papers"
									placeholder="Search papers..."
									value={paperSearchQuery}
									onChange={(e) => setPaperSearchQuery(e.target.value)}
									className="h-10 pl-9 sm:h-9"
								/>
							</div>
							<Select value={paperSortBy} onValueChange={(v) => setPaperSortBy(v as "date_added" | "publish_date" | "title")}>
								<SelectTrigger size="sm" aria-label="Sort papers" className="data-[size=sm]:h-10 sm:data-[size=sm]:h-8">
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
								<div className="grid grid-cols-1 gap-3 sm:gap-4">
									{filteredAndSortedPapers.slice(0, showAllPapers ? filteredAndSortedPapers.length : 3).map((paper, i) => (
										<div key={paper.id} className="min-w-0 animate-rise-in" style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}>
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
						<div className="rounded-xl border-2 border-dashed bg-muted/30 p-8 text-center">
							<div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-muted">
								<Sparkles className="h-7 w-7 text-muted-foreground" />
							</div>
							<h3 className="mb-2 text-lg font-semibold">No Papers Yet</h3>
							<p className="mb-4 text-muted-foreground">Add papers to start analyzing and discussing them.</p>
							<Dialog>
								<DialogTrigger asChild>
									<Button variant="outline">Upload Papers</Button>
								</DialogTrigger>
								<DialogContent>
									<DialogHeader>
										<DialogTitle>Upload New Papers</DialogTitle>
										<DialogDescription>You can upload any additional papers to your library here. They will automatically be added to the project.</DialogDescription>
									</DialogHeader>
									<PdfDropzone onFileSelect={handleFileSelect} onUrlClick={handleLinkClick} />
									{uploadError && <p className="mt-4 text-sm text-destructive">{uploadError}</p>}
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
							<Textarea
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
