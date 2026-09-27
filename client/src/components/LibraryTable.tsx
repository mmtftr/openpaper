"use client";

import {
	Table,
	TableBody,
	TableCell,
	TableHead,
	TableHeader,
	TableRow,
} from "@/components/ui/table";
import { useState, useMemo, useEffect } from "react";
import { AnimatePresence, motion } from "motion/react";
import { api, unwrap } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import { EASE_OUT_SOFT } from "@/lib/motion";
import { Checkbox } from "./ui/checkbox";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Skeleton } from "./ui/skeleton";
import { useSidebar } from "./ui/sidebar";
import {
	Sheet,
	SheetContent,
	SheetTitle,
} from "@/components/ui/sheet";
import { useIsMobile } from "@/hooks/use-mobile";
import { ArrowDown, ArrowDownUp, ArrowUp, ArrowUpDown, CheckCheck, Search, Trash2, X, Tag } from "lucide-react";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuLabel,
	DropdownMenuRadioGroup,
	DropdownMenuRadioItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { PaperPreview } from "./PaperPreview";
import { PaperFiltering, Filter, Sort, NO_TAGS_FILTER_VALUE } from "@/components/PaperFiltering";
import { TagSelector } from "./TagSelector";
import { toast } from "sonner";
import { usePapers } from "@/hooks/usePapers";
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

/** A paper as listed by `usePapers` (`GET /api/paper/all`). */
export type LibraryPaper = NonNullable<ReturnType<typeof usePapers>["papers"]>[number];

type SortKey = keyof LibraryPaper;
type SortDirection = 'ascending' | 'descending';

const MOBILE_SORTS: { label: string; key: SortKey; direction: SortDirection }[] = [
	{ label: "Date added (newest)", key: "created_at", direction: "descending" },
	{ label: "Date added (oldest)", key: "created_at", direction: "ascending" },
	{ label: "Published (newest)", key: "publish_date", direction: "descending" },
	{ label: "Published (oldest)", key: "publish_date", direction: "ascending" },
	{ label: "Title (A–Z)", key: "title", direction: "ascending" },
	{ label: "Title (Z–A)", key: "title", direction: "descending" },
];

/** Rows/cards that fade up on first paint; the rest appear instantly. */
const ENTRANCE_ITEMS = 8;

// Lists scroll the document on phones; on desktop a bounded box scrolls
// internally, either filling the parent (`fillHeight`) or capped.
const CAPPED_HEIGHT = "max-h-[calc(100dvh-16rem)]";

function formatDate(value?: string | null) {
	return value
		? new Date(value).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
		: 'N/A';
}

function entrance(index: number, active: boolean) {
	if (!active || index >= ENTRANCE_ITEMS) return {};
	return { className: "animate-rise-in", style: { animationDelay: `${index * 40}ms` } };
}

function SortHeader({
	label,
	sortKey,
	sortConfig,
	onSort,
}: {
	label: string;
	sortKey: SortKey;
	sortConfig: { key: SortKey; direction: SortDirection } | null;
	onSort: (key: SortKey) => void;
}) {
	const active = sortConfig?.key === sortKey;
	const Icon = !active ? ArrowUpDown : sortConfig.direction === 'ascending' ? ArrowUp : ArrowDown;
	return (
		<button
			type="button"
			onClick={() => onSort(sortKey)}
			className={cn(
				"-ml-2 inline-flex h-8 items-center gap-1.5 rounded-md px-2 text-xs font-medium uppercase tracking-wide transition-colors duration-150 hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-brand/40 focus-visible:outline-none",
				active ? "text-foreground" : "text-muted-foreground",
			)}
		>
			{label}
			<Icon className={cn("size-3.5", active ? "text-brand" : "opacity-50")} />
		</button>
	);
}

const headerLabel = "text-xs font-medium uppercase tracking-wide text-muted-foreground";

/** Placeholder for the toolbar and list: table rows on desktop, cards on phones. */
export function LibrarySkeleton() {
	return (
		<div className="flex flex-col gap-3">
			<div className="flex items-center gap-2">
				<Skeleton className="h-10 flex-1 md:max-w-2xl" />
				<Skeleton className="h-10 w-24" />
				<Skeleton className="size-10 md:hidden" />
			</div>
			<div className="hidden overflow-hidden rounded-xl border bg-card md:block">
				<div className="flex h-11 items-center gap-6 border-b px-4">
					<Skeleton className="size-4" />
					<Skeleton className="h-3 w-16" />
					<Skeleton className="ml-auto h-3 w-40" />
				</div>
				{Array.from({ length: 8 }).map((_, i) => (
					<div key={i} className="flex items-center gap-6 border-b px-4 py-4 last:border-0">
						<Skeleton className="size-4 shrink-0" />
						<div className="min-w-0 flex-1 space-y-2">
							<Skeleton className="h-4 w-full max-w-md" />
							<Skeleton className="h-4 w-2/3 max-w-xs" />
						</div>
						<Skeleton className="hidden h-4 w-36 shrink-0 lg:block" />
						<div className="hidden shrink-0 gap-1 xl:flex">
							<Skeleton className="h-5 w-20" />
							<Skeleton className="h-5 w-14" />
						</div>
						<Skeleton className="h-4 w-24 shrink-0" />
					</div>
				))}
			</div>
			<div className="divide-y overflow-hidden rounded-xl border bg-card md:hidden">
				{Array.from({ length: 6 }).map((_, i) => (
					<div key={i} className="flex gap-3 px-4 py-3.5">
						<Skeleton className="mt-0.5 size-4 shrink-0" />
						<div className="min-w-0 flex-1 space-y-2">
							<Skeleton className="h-4 w-full" />
							<Skeleton className="h-4 w-3/4" />
							<Skeleton className="h-3 w-1/2" />
						</div>
					</div>
				))}
			</div>
		</div>
	);
}

interface LibraryTableProps extends React.HTMLAttributes<HTMLDivElement> {
	selectable?: boolean;
	onSelectFiles?: (papers: LibraryPaper[], action: string) => void;
	actionOptions?: string[];
	projectPaperIds?: string[];
	handleDelete?: (paperId: string) => Promise<void>;
	setPapers?: (papers: LibraryPaper[]) => void;
	onUploadClick?: () => void;
	/** On desktop, fill the parent's height and scroll the table inside it. */
	fillHeight?: boolean;
}

export function LibraryTable({
	selectable: selectableProp,
	onSelectFiles,
	actionOptions = [],
	projectPaperIds = [],
	handleDelete,
	onUploadClick,
	fillHeight = false,
	className,
	setPapers: _setPapers,
	...props
}: LibraryTableProps) {
	const selectable = selectableProp ?? (onSelectFiles ? true : false);
	const { papers, error: papersFetchError, isLoading, mutate } = usePapers();
	const { state: sidebarState } = useSidebar();
	const isMobile = useIsMobile();
	const [selectedPapers, setSelectedPapers] = useState<Set<string>>(new Set());
	const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
	const [searchTerm, setSearchTerm] = useState('');
	const [filters, setFilters] = useState<Filter[]>([]);
	const [sortConfig, setSortConfig] = useState<{ key: SortKey; direction: SortDirection } | null>({ key: 'created_at', direction: 'descending' });
	const [selectedPaperForPreview, setSelectedPaperForPreview] = useState<LibraryPaper | null>(null);
	const [taggingPopoverOpen, setTaggingPopoverOpen] = useState(false);
	const [expandedTags, setExpandedTags] = useState<Set<string>>(new Set());
	// Only the first paint of the list gets the entrance; rows that come back
	// after a search or filter change just appear.
	const [entranceActive, setEntranceActive] = useState(true);

	useEffect(() => {
		const timer = window.setTimeout(() => setEntranceActive(false), 800);
		return () => window.clearTimeout(timer);
	}, []);

	const sort: Sort = { type: "publish_date", order: "desc" };

	const setPaper = (paperId: string, updatedPaper: LibraryPaper) => {
		mutate(
			(currentPapers: LibraryPaper[] | undefined) => {
				if (!currentPapers) return [];
				return currentPapers.map(p => (p.id === paperId ? updatedPaper : p));
			},
			{ revalidate: false }
		);

		if (selectedPaperForPreview && selectedPaperForPreview.id === paperId) {
			setSelectedPaperForPreview(updatedPaper);
		}
	};

	const processedPapers = useMemo(() => {
		let filteredPapers = [...(papers || [])];

		if (searchTerm) {
			filteredPapers = filteredPapers.filter(paper => {
				const term = searchTerm.toLowerCase();
				return (
					paper.title?.toLowerCase().includes(term) ||
					paper.authors?.join(', ').toLowerCase().includes(term) ||
					paper.institutions?.join(', ').toLowerCase().includes(term) ||
					paper.keywords?.join(', ').toLowerCase().includes(term)
				);
			});
		}

		if (filters.length > 0) {
			filteredPapers = filteredPapers.filter(paper => {
				return filters.every(filter => {
					if (filter.type === 'author') {
						return paper.authors?.includes(filter.value);
					}
					if (filter.type === 'keyword') {
						return paper.keywords?.includes(filter.value);
					}
					if (filter.type === 'tag') {
						if (filter.value === NO_TAGS_FILTER_VALUE) {
							return !paper.tags?.length;
						}
						return paper.tags?.some(t => t.name === filter.value);
					}
					if (filter.type === 'status') {
						return paper.status === filter.value;
					}
					return true;
				});
			});
		}

		if (sortConfig !== null) {
			filteredPapers.sort((a, b) => {
				const key = sortConfig.key;
				const aVal = a[key];
				const bVal = b[key];

				if (aVal === undefined || aVal === null) return 1;
				if (bVal === undefined || bVal === null) return -1;

				let comparison = 0;
				if (key === 'created_at' || key === 'publish_date') {
					comparison = new Date(aVal as string).getTime() - new Date(bVal as string).getTime();
				} else if (key === 'tags') {
					const aTags = (aVal as { name: string }[]).map(t => t.name).join(', ');
					const bTags = (bVal as { name: string }[]).map(t => t.name).join(', ');
					comparison = aTags.localeCompare(bTags);
				} else if (Array.isArray(aVal) && Array.isArray(bVal)) {
					comparison = aVal.join(', ').localeCompare(bVal.join(', '));
				} else if (typeof aVal === 'string' && typeof bVal === 'string') {
					comparison = aVal.localeCompare(bVal);
				} else if (typeof aVal === 'number' && typeof bVal === 'number') {
					comparison = aVal - bVal;
				}


				return sortConfig.direction === 'ascending' ? comparison : -comparison;
			});
		}

		return filteredPapers;
	}, [papers, searchTerm, filters, sortConfig]);

	const availablePapers = useMemo(() => {
		return processedPapers.filter(p => !projectPaperIds.includes(p.id));
	}, [processedPapers, projectPaperIds]);

	const requestSort = (key: SortKey) => {
		let direction: SortDirection = 'ascending';
		if (sortConfig && sortConfig.key === key && sortConfig.direction === 'ascending') {
			direction = 'descending';
		}
		setSortConfig({ key, direction });
	};

	const handleSelectAll = (checked: boolean) => {
		if (checked) {
			setSelectedPapers(new Set(availablePapers.map((p) => p.id)));
		} else {
			setSelectedPapers(new Set());
		}
	};

	const handleSelect = (paperId: string, checked?: boolean) => {
		const newSelectedPapers = new Set(selectedPapers);
		const isCurrentlySelected = newSelectedPapers.has(paperId);

		const shouldBeSelected = checked !== undefined ? checked : !isCurrentlySelected;

		if (shouldBeSelected) {
			newSelectedPapers.add(paperId);
		} else {
			newSelectedPapers.delete(paperId);
		}
		setSelectedPapers(newSelectedPapers);
	};

	const handleAction = (action: string) => {
		if (onSelectFiles) {
			const selectedItems = (papers || []).filter((p) => selectedPapers.has(p.id));
			onSelectFiles(selectedItems, action);
			setSelectedPapers(new Set());
		}
	};

	const handleDeletePapers = async () => {
		if (!handleDelete) return;

		const paperIdsToDelete = Array.from(selectedPapers);
		const deletePromises = paperIdsToDelete.map(id => handleDelete(id));

		try {
			await Promise.all(deletePromises);
			toast.success(`Successfully deleted ${paperIdsToDelete.length} paper(s).`);
			mutate(); // Revalidate the papers list
		} catch (error) {
			console.error("Failed to delete some papers:", error);
			toast.error("An error occurred while deleting papers.");
		}

		setSelectedPapers(new Set());
	};

	const toggleExpandedTags = (paperId: string) => {
		setExpandedTags(prev => {
			const newSet = new Set(prev);
			if (newSet.has(paperId)) {
				newSet.delete(paperId);
			} else {
				newSet.add(paperId);
			}
			return newSet;
		});
	};

	const addFilter = (filter: Filter) => {
		if (!filters.some(f => f.type === filter.type && f.value === filter.value)) {
			setFilters([...filters, filter]);
		}
	};

	const handleTagClick = (tagName: string) => addFilter({ type: 'tag', value: tagName });

	const handleRemoveTag = async (paperId: string, tagId: string) => {
		try {
			await unwrap(api.DELETE("/api/paper/tag/papers/{paper_id}/tags/{tag_id}", {
				params: { path: { paper_id: paperId, tag_id: tagId } },
			}));
			mutate(); // Revalidate the papers list
		} catch (error) {
			console.error("Failed to remove tag", error);
			toast.error("Failed to remove tag.");
		}
	};

	const clearSearchAndFilters = () => {
		setSearchTerm('');
		setFilters([]);
	};

	if (isLoading) {
		return <LibrarySkeleton />;
	}

	// A failed revalidation keeps the cached list on screen.
	if (papersFetchError && !papers) {
		return (
			<div className="flex items-center justify-center py-12">
				<div className="text-destructive">
					Failed to load papers: {papersFetchError instanceof Error ? papersFetchError.message : String(papersFetchError)}
				</div>
			</div>
		);
	}

	const numCols = 7 + (selectable ? 1 : 0);
	const allAvailableSelected = availablePapers.length > 0 && selectedPapers.size === availablePapers.length;
	const totalCount = (papers || []).length;
	const isNarrowed = processedPapers.length !== totalCount;
	const hasSelection = selectedPapers.size > 0;
	const showBulkBar = selectable && hasSelection;
	const activeMobileSort = sortConfig ? `${String(sortConfig.key)}:${sortConfig.direction}` : "";

	const emptyContent = searchTerm || filters.length > 0 ? (
		<div className="flex flex-col items-center gap-3 py-10 text-center">
			<p className="text-sm text-muted-foreground">No papers match your search.</p>
			<Button variant="outline" size="sm" onClick={clearSearchAndFilters}>Clear search and filters</Button>
		</div>
	) : (
		<div className="flex flex-col items-center gap-4 px-4 py-10 text-center">
			<div className="text-muted-foreground">
				<p className="mb-1 text-base font-medium text-foreground">No papers in your library yet</p>
				<p className="text-sm">Upload a paper and it will show up here.</p>
			</div>
			{onUploadClick && (
				<Button className="bg-brand text-brand-foreground hover:bg-brand/90" onClick={onUploadClick}>
					Upload your first paper
				</Button>
			)}
		</div>
	);

	const renderTagChip = (paperId: string, tag: { id: string; name: string }) => (
		<span
			key={tag.id}
			className="group/tag inline-flex max-w-full items-center rounded-md bg-brand/10 text-xs font-medium text-brand"
		>
			<button
				type="button"
				onClick={(e) => { e.stopPropagation(); handleTagClick(tag.name); }}
				className="truncate rounded-md py-0.5 pl-2 pr-1 focus-visible:ring-2 focus-visible:ring-brand/40 focus-visible:outline-none"
				title={`Filter by ${tag.name}`}
			>
				{tag.name}
			</button>
			<button
				type="button"
				aria-label={`Remove tag ${tag.name}`}
				onClick={(e) => {
					e.stopPropagation();
					handleRemoveTag(paperId, tag.id);
				}}
				className="mr-0.5 rounded p-0.5 opacity-0 transition-opacity duration-150 group-hover/tag:opacity-100 hover:bg-brand/15 focus-visible:opacity-100 focus-visible:outline-none pointer-coarse:opacity-100"
			>
				<X className="size-3" />
			</button>
		</span>
	);

	const scrollBoxHeight = fillHeight ? "md:h-full" : CAPPED_HEIGHT;

	return (
		<div
			className={cn("flex w-full min-w-0 flex-col gap-3", fillHeight && "md:h-full md:min-h-0", className)}
			{...props}
		>
			{/* Toolbar: search, filter, and (phones) sort */}
			<div className="flex items-center gap-2">
				<div className="relative min-w-0 flex-1 md:max-w-2xl">
					<Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
					<Input
						type="search"
						aria-label="Search papers"
						placeholder="Search papers"
						value={searchTerm}
						onChange={(e) => setSearchTerm(e.target.value)}
						className="h-10 pl-9"
					/>
				</div>
				<PaperFiltering
					papers={papers || []}
					onFilterChange={setFilters}
					onSortChange={() => { }}
					filters={filters}
					sort={sort}
					showSort={false}
				/>
				<DropdownMenu>
					<DropdownMenuTrigger asChild>
						<Button variant="outline" size="icon-lg" className="shrink-0 md:hidden" aria-label="Sort papers">
							<ArrowDownUp className="size-4" />
						</Button>
					</DropdownMenuTrigger>
					<DropdownMenuContent align="end" className="w-52">
						<DropdownMenuLabel>Sort by</DropdownMenuLabel>
						<DropdownMenuRadioGroup
							value={activeMobileSort}
							onValueChange={(value) => {
								const option = MOBILE_SORTS.find(o => `${String(o.key)}:${o.direction}` === value);
								if (option) setSortConfig({ key: option.key, direction: option.direction });
							}}
						>
							{MOBILE_SORTS.map(option => (
								<DropdownMenuRadioItem key={option.label} value={`${String(option.key)}:${option.direction}`}>
									{option.label}
								</DropdownMenuRadioItem>
							))}
						</DropdownMenuRadioGroup>
					</DropdownMenuContent>
				</DropdownMenu>
				{isNarrowed && (
					<span className="ml-auto hidden shrink-0 text-sm text-muted-foreground tabular-nums md:block">
						{processedPapers.length} of {totalCount}
					</span>
				)}
			</div>

			{(filters.length > 0 || (isNarrowed && searchTerm)) && (
				<div className="flex flex-wrap items-center gap-1.5">
					{filters.map(filter => (
						<span
							key={`${filter.type}-${filter.value}`}
							className="inline-flex h-7 items-center gap-1 rounded-full border bg-secondary pr-1 pl-2.5 text-xs text-secondary-foreground"
						>
							<span className="text-muted-foreground">{filter.type}:</span>
							<span className="max-w-[12rem] truncate font-medium">
								{filter.value === NO_TAGS_FILTER_VALUE ? 'No tags' : filter.value}
							</span>
							<button
								type="button"
								aria-label={`Remove ${filter.type} filter`}
								className="rounded-full p-1 text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
								onClick={() => setFilters(filters.filter(f => !(f.type === filter.type && f.value === filter.value)))}
							>
								<X className="size-3" />
							</button>
						</span>
					))}
					<span className="px-1 text-xs text-muted-foreground tabular-nums md:hidden">
						{processedPapers.length} of {totalCount}
					</span>
					{filters.length > 0 && (
						<Button variant="ghost" size="sm" className="h-7 px-2 text-xs text-muted-foreground" onClick={() => setFilters([])}>
							Clear filters
						</Button>
					)}
				</div>
			)}

			<div
				className={cn(
					"grid min-w-0 gap-4",
					fillHeight && "md:min-h-0 md:flex-1 md:grid-rows-[minmax(0,1fr)]",
					selectedPaperForPreview && (sidebarState === 'expanded'
						? "md:grid-cols-[minmax(0,1fr)_320px]"
						: "md:grid-cols-[minmax(0,1fr)_384px]"),
				)}
			>
				<div className={cn("relative min-w-0", fillHeight && "md:min-h-0")}>
					{/* Desktop: table */}
					<div className={cn("hidden overflow-hidden rounded-xl border bg-card md:block", fillHeight && "md:h-full")}>
						<div className={cn("overflow-auto", scrollBoxHeight, showBulkBar && "pb-16")}>
							<Table noWrapperOverflow>
								<TableHeader className="sticky top-0 z-10 [&_th]:bg-card/80 [&_th]:backdrop-blur-md [&_tr]:border-b-0">
									<TableRow className="hover:bg-transparent [&_th]:shadow-[inset_0_-1px_0_var(--border)]">
										{selectable && (
											<TableHead className="w-12 pl-4 text-center">
												<Checkbox
													aria-label="Select all papers"
													checked={allAvailableSelected}
													onCheckedChange={(checked) => handleSelectAll(!!checked)}
													disabled={availablePapers.length === 0}
												/>
											</TableHead>
										)}
										<TableHead
											className={cn(selectedPaperForPreview ? "min-w-[17rem]" : "min-w-[20rem]", !selectable && "pl-4")}
											aria-sort={sortConfig?.key === 'title' ? sortConfig.direction : 'none'}
										>
											<SortHeader label="Title" sortKey="title" sortConfig={sortConfig} onSort={requestSort} />
										</TableHead>
										<TableHead className={cn("min-w-[11rem]", headerLabel)}>Authors</TableHead>
										<TableHead className={cn("min-w-[11rem]", headerLabel, selectedPaperForPreview && "hidden")}>Organizations</TableHead>
										<TableHead className={cn("min-w-[12rem]", headerLabel)}>Keywords</TableHead>
										<TableHead className={cn("min-w-[9rem]", headerLabel)}>Tags</TableHead>
										<TableHead
											className="min-w-[7.5rem]"
											aria-sort={sortConfig?.key === 'created_at' ? sortConfig.direction : 'none'}
										>
											<SortHeader label="Added" sortKey="created_at" sortConfig={sortConfig} onSort={requestSort} />
										</TableHead>
										<TableHead
											className={cn("min-w-[7.5rem] pr-4", selectedPaperForPreview && "hidden")}
											aria-sort={sortConfig?.key === 'publish_date' ? sortConfig.direction : 'none'}
										>
											<SortHeader label="Published" sortKey="publish_date" sortConfig={sortConfig} onSort={requestSort} />
										</TableHead>
									</TableRow>
								</TableHeader>
								<TableBody>
									{processedPapers.length > 0 ? (
										processedPapers.map((paper, index) => {
											const isAlreadyInProject = projectPaperIds.includes(paper.id);
											const isSelected = selectedPapers.has(paper.id);
											const isPreviewed = selectedPaperForPreview?.id === paper.id;
											const enter = entrance(index, entranceActive);
											return (
												<TableRow
													key={paper.id}
													data-state={isSelected ? "selected" : undefined}
													onClick={() => {
														if (selectable && !isAlreadyInProject) {
															handleSelect(paper.id)
														}
													}}
													style={enter.style}
													className={cn(
														"border-b border-border/70 transition-colors duration-150 hover:bg-muted/40 data-[state=selected]:bg-brand/[0.06] data-[state=selected]:hover:bg-brand/10",
														isPreviewed && !isSelected && "bg-muted/50",
														(selectable && !isAlreadyInProject) || !selectable ? "cursor-pointer" : "",
														isAlreadyInProject && "opacity-60",
														enter.className,
													)}
												>
													{selectable && (
														<TableCell
															className="pl-4 text-center"
															onClick={(e) => e.stopPropagation()}
														>
															{isAlreadyInProject ? (
																<CheckCheck className="mx-auto size-4 text-emerald-600 dark:text-emerald-400" aria-label="Already in project" />
															) : (
																<Checkbox
																	aria-label={`Select ${paper.title || 'Untitled'}`}
																	checked={isSelected}
																	onCheckedChange={(checked) =>
																		handleSelect(paper.id, !!checked)
																	}
																/>
															)}
														</TableCell>
													)}
													<TableCell className={cn("py-3 pr-4 whitespace-normal", !selectable && "pl-4")}>
														<button
															type="button"
															className={cn(
																"line-clamp-2 rounded-sm text-left text-sm leading-snug font-semibold break-words transition-colors duration-150 hover:text-brand focus-visible:ring-2 focus-visible:ring-brand/40 focus-visible:ring-offset-2 focus-visible:ring-offset-card focus-visible:outline-none",
																isPreviewed && "text-brand",
															)}
															onClick={(e) => {
																e.stopPropagation();
																setSelectedPaperForPreview(paper);
															}}
														>
															{paper.title || 'Untitled'}
														</button>
														{paper.processing && (
															<span className="mt-1.5 inline-flex items-center rounded-full border px-2 py-px text-[10px] font-medium text-muted-foreground">
																Processing
															</span>
														)}
													</TableCell>
													<TableCell className="py-3 pr-4 whitespace-normal">
														<div className="line-clamp-2 text-sm leading-snug break-words text-muted-foreground">
															{paper.authors?.length ? paper.authors.join(", ") : <span className="text-muted-foreground/60">No authors</span>}
														</div>
													</TableCell>
													<TableCell className={cn("py-3 pr-4 whitespace-normal", selectedPaperForPreview && "hidden")}>
														<div className="line-clamp-2 text-sm leading-snug break-words text-muted-foreground">
															{paper.institutions?.length ? paper.institutions.join(", ") : <span className="text-muted-foreground/60">—</span>}
														</div>
													</TableCell>
													<TableCell className="py-3 pr-4 whitespace-normal">
														{paper.keywords?.length ? (
															<div className="flex flex-wrap items-center gap-1">
																{paper.keywords.slice(0, 2).map((keyword, i) => (
																	<span
																		key={i}
																		className="inline-block max-w-[11rem] truncate rounded-md bg-muted px-1.5 py-0.5 text-xs text-muted-foreground"
																		title={keyword}
																	>
																		{keyword}
																	</span>
																))}
																{paper.keywords.length > 2 && (
																	<span className="text-xs text-muted-foreground/80 tabular-nums">
																		+{paper.keywords.length - 2}
																	</span>
																)}
															</div>
														) : (
															<span className="text-sm text-muted-foreground/60">—</span>
														)}
													</TableCell>
													<TableCell className="py-3 pr-4 whitespace-normal">
														{paper.tags?.length ? (
															<div className="flex flex-wrap items-center gap-1">
																{(expandedTags.has(paper.id) ? paper.tags : paper.tags.slice(0, 3)).map((tag) => renderTagChip(paper.id, tag))}
																{paper.tags.length > 3 && !expandedTags.has(paper.id) && (
																	<button
																		type="button"
																		onClick={(e) => { e.stopPropagation(); toggleExpandedTags(paper.id); }}
																		className="rounded px-1 text-xs text-muted-foreground hover:text-foreground"
																	>
																		+{paper.tags.length - 3} more
																	</button>
																)}
															</div>
														) : (
															<button
																type="button"
																className="rounded text-xs text-muted-foreground/70 transition-colors hover:text-foreground"
																onClick={(e) => {
																	e.stopPropagation();
																	addFilter({ type: 'tag', value: NO_TAGS_FILTER_VALUE });
																}}
															>
																No tags
															</button>
														)}
													</TableCell>
													<TableCell className="py-3 pr-4 text-sm whitespace-nowrap text-muted-foreground tabular-nums">
														{formatDate(paper.created_at)}
													</TableCell>
													<TableCell className={cn("py-3 pr-4 text-sm whitespace-nowrap text-muted-foreground tabular-nums", selectedPaperForPreview && "hidden")}>
														{formatDate(paper.publish_date)}
													</TableCell>
												</TableRow>
											);
										})
									) : (
										<TableRow className="hover:bg-transparent">
											<TableCell colSpan={numCols} className="whitespace-normal">
												{emptyContent}
											</TableCell>
										</TableRow>
									)}
								</TableBody>
							</Table>
						</div>
					</div>

					{/* Phones: card list */}
					{selectable && availablePapers.length > 0 && (
						<label className="-mt-1 flex h-10 w-fit cursor-pointer items-center gap-3 px-3 text-sm text-muted-foreground md:hidden">
							<Checkbox
								checked={allAvailableSelected}
								onCheckedChange={(checked) => handleSelectAll(!!checked)}
							/>
							Select all
						</label>
					)}
					{processedPapers.length > 0 ? (
						<ul className={cn("divide-y divide-border/70 overflow-hidden rounded-xl border bg-card md:hidden", showBulkBar && "mb-20")}>
							{processedPapers.map((paper, index) => {
								const isAlreadyInProject = projectPaperIds.includes(paper.id);
								const isSelected = selectedPapers.has(paper.id);
								const authors = paper.authors ?? [];
								const tags = paper.tags ?? [];
								const enter = entrance(index, entranceActive);
								return (
									<li
										key={paper.id}
										style={enter.style}
										className={cn(
											"flex items-stretch transition-colors duration-150",
											isSelected && "bg-brand/[0.06]",
											isAlreadyInProject && "opacity-60",
											enter.className,
										)}
									>
										{selectable && (
											<div className="flex w-12 shrink-0 justify-center pt-4">
												{isAlreadyInProject ? (
													<CheckCheck className="size-4 text-emerald-600 dark:text-emerald-400" aria-label="Already in project" />
												) : (
													// The pseudo-element widens the hit area to 40px.
													<Checkbox
														aria-label={`Select ${paper.title || 'Untitled'}`}
														checked={isSelected}
														onCheckedChange={(checked) => handleSelect(paper.id, !!checked)}
														className="relative after:absolute after:-inset-3 after:content-['']"
													/>
												)}
											</div>
										)}
										<button
											type="button"
											onClick={() => setSelectedPaperForPreview(paper)}
											className={cn(
												"min-w-0 flex-1 py-3 pr-4 text-left transition-colors duration-150 focus-visible:bg-muted/60 focus-visible:outline-none active:bg-muted/60",
												!selectable && "pl-4",
											)}
										>
											<span className="line-clamp-2 text-[15px] leading-snug font-semibold break-words">
												{paper.title || 'Untitled'}
											</span>
											<span className="mt-1 flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
												<span className="truncate">
													{authors.length ? authors[0] : 'No authors'}
													{authors.length > 1 && <span className="tabular-nums"> +{authors.length - 1}</span>}
												</span>
												<span aria-hidden className="text-muted-foreground/50">·</span>
												<span className="shrink-0 tabular-nums">{formatDate(paper.created_at)}</span>
											</span>
											{(paper.processing || tags.length > 0) && (
												<span className="mt-2 flex flex-wrap items-center gap-1">
													{paper.processing && (
														<span className="rounded-full border px-2 py-px text-[10px] font-medium text-muted-foreground">
															Processing
														</span>
													)}
													{tags.slice(0, 2).map(tag => (
														<span key={tag.id} className="max-w-[9rem] truncate rounded-md bg-brand/10 px-1.5 py-px text-[11px] font-medium text-brand">
															{tag.name}
														</span>
													))}
													{tags.length > 2 && (
														<span className="text-[11px] text-muted-foreground tabular-nums">+{tags.length - 2}</span>
													)}
												</span>
											)}
										</button>
									</li>
								);
							})}
						</ul>
					) : (
						<div className="rounded-xl border bg-card md:hidden">{emptyContent}</div>
					)}

					{/* Bulk actions: floating over the table on desktop, above the tab bar on phones. */}
					<AnimatePresence>
						{showBulkBar && (
							<motion.div
								key="bulk-bar"
								role="toolbar"
								aria-label="Selected papers"
								initial={{ opacity: 0, y: 12 }}
								animate={{ opacity: 1, y: 0 }}
								exit={{ opacity: 0, y: 12, transition: { duration: 0.15 } }}
								transition={{ duration: 0.22, ease: EASE_OUT_SOFT }}
								className="fixed inset-x-0 bottom-[calc(var(--app-tabbar-h)+env(safe-area-inset-bottom)+0.75rem)] z-40 mx-auto flex w-fit max-w-[calc(100%-1.5rem)] items-center gap-1 rounded-full border bg-popover/95 p-1.5 shadow-lg shadow-black/10 backdrop-blur-md md:absolute md:bottom-4"
							>
								<Button
									variant="ghost"
									size="icon"
									className="rounded-full text-muted-foreground"
									aria-label="Clear selection"
									onClick={() => setSelectedPapers(new Set())}
								>
									<X className="size-4" />
								</Button>
								<span className="pr-2 text-sm font-medium whitespace-nowrap tabular-nums">
									{selectedPapers.size}<span className="max-[359px]:hidden"> selected</span>
								</span>
								{onSelectFiles && actionOptions.map((action) => (
									<Button
										key={action}
										size="sm"
										onClick={() => handleAction(action)}
										className="h-9 rounded-full bg-brand px-4 font-medium text-brand-foreground hover:bg-brand/90"
									>
										{action}
									</Button>
								))}
								<DropdownMenu open={taggingPopoverOpen} onOpenChange={setTaggingPopoverOpen}>
									<DropdownMenuTrigger asChild>
										<Button variant="ghost" size="sm" className="h-9 rounded-full px-3" aria-label="Tag selected papers">
											<Tag className="size-4" />
											<span className="max-[359px]:hidden">Tag</span>
										</Button>
									</DropdownMenuTrigger>
									<DropdownMenuContent className="w-80 max-w-[calc(100vw-1.5rem)]" side="top" align="center">
										<TagSelector
											paperIds={Array.from(selectedPapers)}
											onTagsApplied={() => {
												setTaggingPopoverOpen(false);
												mutate();
											}}
										/>
									</DropdownMenuContent>
								</DropdownMenu>
								{handleDelete && (
									<Button
										variant="ghost"
										size="icon"
										className="rounded-full text-destructive hover:bg-destructive/10 hover:text-destructive"
										aria-label={`Delete ${selectedPapers.size} selected`}
										onClick={() => setConfirmDeleteOpen(true)}
									>
										<Trash2 className="size-4" />
									</Button>
								)}
							</motion.div>
						)}
					</AnimatePresence>
				</div>

				{selectedPaperForPreview && !isMobile && (
					<div
						className={cn(
							"hidden min-h-0 animate-in flex-col overflow-hidden duration-200 ease-out-soft fade-in slide-in-from-right-4 md:flex",
							fillHeight ? "md:h-full" : CAPPED_HEIGHT,
						)}
					>
						<PaperPreview paper={selectedPaperForPreview} onClose={() => setSelectedPaperForPreview(null)} setPaper={setPaper} />
					</div>
				)}
			</div>

			{selectedPaperForPreview && isMobile && (
				<Sheet open={!!selectedPaperForPreview} onOpenChange={(open) => { if (!open) setSelectedPaperForPreview(null); }}>
					<SheetContent side="bottom" className="flex h-[90dvh] w-full flex-col gap-0 overflow-hidden rounded-t-2xl p-0 [&>button]:hidden">
						<SheetTitle className="sr-only">{selectedPaperForPreview.title || 'Paper preview'}</SheetTitle>
						<div aria-hidden className="mx-auto mt-2 mb-1 h-1 w-10 shrink-0 rounded-full bg-muted-foreground/25" />
						<div className="min-h-0 flex-1 overflow-y-auto">
							<PaperPreview
								paper={selectedPaperForPreview}
								onClose={() => setSelectedPaperForPreview(null)}
								setPaper={setPaper}
								className="rounded-none border-0"
							/>
						</div>
					</SheetContent>
				</Sheet>
			)}

			<AlertDialog open={confirmDeleteOpen} onOpenChange={setConfirmDeleteOpen}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Delete {selectedPapers.size} {selectedPapers.size === 1 ? 'paper' : 'papers'}?
						</AlertDialogTitle>
						<AlertDialogDescription>
							{selectedPapers.size === 1 ? 'The selected paper' : `All ${selectedPapers.size} selected papers`} will be permanently deleted. This cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleDeletePapers}
							className="bg-destructive text-white hover:bg-destructive/90"
						>
							Delete
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
