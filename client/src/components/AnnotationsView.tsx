import { useEffect, useMemo, useRef, useState } from 'react';

import {
	HighlightColor,
	PaperHighlight,
	PaperHighlightAnnotation,
} from '@/lib/schema';
import { RenderedHighlightPosition } from '@/components/reader';
import { smoothScrollTo } from '@/lib/animation';
import { BasicUser } from "@/lib/auth";
import { cn } from '@/lib/utils';
import { CollapsibleNoteText } from '@/components/CollapsibleNoteText';
import { NoteThread } from '@/components/notes/NoteThread';

const ITEM_BG_MAP: Record<HighlightColor, string> = {
	yellow: "bg-yellow-50 dark:bg-yellow-950/20",
	green:  "bg-green-50 dark:bg-green-950/20",
	blue:   "bg-blue-50 dark:bg-blue-950/20",
	pink:   "bg-pink-50 dark:bg-pink-950/20",
	purple: "bg-purple-50 dark:bg-purple-950/20",
};

/** Left accent for quoted PDF snippet (matches thread highlight color). */
const QUOTE_ACCENT_BORDER: Record<HighlightColor, string> = {
	yellow: "border-yellow-500 dark:border-yellow-400",
	green: "border-green-600 dark:border-green-500",
	blue: "border-blue-500 dark:border-blue-400",
	pink: "border-pink-500 dark:border-pink-400",
	purple: "border-purple-500 dark:border-purple-400",
};

function annotationCreatedMs(iso: string | undefined): number {
	if (!iso) return NaN;
	const t = Date.parse(iso);
	return Number.isFinite(t) ? t : NaN;
}

/** Newest annotation in the thread (ms since epoch); used for ordering threads latest → oldest */
function threadLastActivityMs(
	annotationMap: Map<string, PaperHighlightAnnotation[]>,
	highlightId: string
): number {
	const anns = annotationMap.get(highlightId);
	if (!anns?.length) return 0;
	let max = 0;
	for (const ann of anns) {
		const t = annotationCreatedMs(ann.created_at);
		if (Number.isFinite(t)) max = Math.max(max, t);
	}
	return max;
}

interface AnnotationsViewProps {
	highlights: PaperHighlight[];
	annotations: PaperHighlightAnnotation[];
	onHighlightClick: (highlight: PaperHighlight) => void;
	activeHighlight?: PaperHighlight | null;
	user: BasicUser;
	renderedHighlightPositions?: Map<string, RenderedHighlightPosition>;
	addAnnotation?: (highlightId: string, content: string) => Promise<PaperHighlightAnnotation>;
	updateAnnotation?: (annotationId: string, content: string) => Promise<unknown> | void;
	removeAnnotation?: (annotationId: string) => void;
	readonly?: boolean;
}

interface AnnotationThread {
	highlight: PaperHighlight;
	annotations: PaperHighlightAnnotation[];
}

type ThreadFilter = 'all' | 'ai' | 'mine';

const THREAD_FILTERS: { value: ThreadFilter; label: string }[] = [
	{ value: 'all', label: 'All' },
	{ value: 'ai', label: 'AI' },
	{ value: 'mine', label: 'Mine' },
];

/** AI threads hang off the highlights the ingestion job / assistant made. */
function isAiThread(thread: AnnotationThread): boolean {
	return thread.highlight.role === 'assistant';
}

export function AnnotationsView({
	highlights,
	annotations,
	onHighlightClick,
	activeHighlight,
	user,
	renderedHighlightPositions,
	addAnnotation,
	updateAnnotation,
	removeAnnotation,
	readonly = false,
}: AnnotationsViewProps) {
	const firstAnnotationRefs = useRef<Record<string, HTMLDivElement | null>>({});
	const scrollContainerRef = useRef<HTMLDivElement | null>(null);
	const [filter, setFilter] = useState<ThreadFilter>('all');

	const threads = useMemo<AnnotationThread[]>(() => {
		const annotationMap = new Map<string, PaperHighlightAnnotation[]>();
		for (const ann of annotations) {
			const existing = annotationMap.get(ann.highlight_id) ?? [];
			existing.push(ann);
			annotationMap.set(ann.highlight_id, existing);
		}

		const annotatedHighlights = highlights.filter((h) => {
			if (!h.id) return false;
			if (!annotationMap.has(h.id)) return false;
			if (h.role === 'user') return true;
			if (h.position) return true;
			if (h.id && renderedHighlightPositions?.has(h.id)) return true;
			if (h.role === 'assistant' && h.raw_text?.trim()) return true;
			return false;
		});

		const seenIds = new Set<string>();
		const dedupedHighlights = annotatedHighlights.filter((h) => {
			if (!h.id || seenIds.has(h.id)) return false;
			seenIds.add(h.id);
			return true;
		});

		const sorted = [...dedupedHighlights].sort((a, b) => {
			const idA = a.id!;
			const idB = b.id!;
			const tA = threadLastActivityMs(annotationMap, idA);
			const tB = threadLastActivityMs(annotationMap, idB);
			if (tB !== tA) return tB - tA;
			return idB.localeCompare(idA);
		});

		// Each thread orders its own notes (oldest first).
		return sorted.map((highlight) => ({
			highlight,
			annotations: annotationMap.get(highlight.id!) ?? [],
		}));
	}, [highlights, annotations, renderedHighlightPositions]);

	const aiCount = useMemo(() => threads.filter(isAiThread).length, [threads]);
	const filterCounts: Record<ThreadFilter, number> = {
		all: threads.length,
		ai: aiCount,
		mine: threads.length - aiCount,
	};
	const visibleThreads = useMemo(
		() =>
			filter === 'all'
				? threads
				: threads.filter((t) => isAiThread(t) === (filter === 'ai')),
		[threads, filter]
	);

	// A thread activated from the PDF (or "Open in Annotations") must be on
	// screen, so a filter that hides it gives way. Only on activation: keyed on
	// the id alone, so picking a chip that hides the current thread still works.
	const filterRef = useRef(filter);
	filterRef.current = filter;
	const threadsRef = useRef(threads);
	threadsRef.current = threads;
	useEffect(() => {
		const id = activeHighlight?.id;
		const current = filterRef.current;
		if (!id || current === 'all') return;
		const thread = threadsRef.current.find((t) => t.highlight.id === id);
		if (thread && isAiThread(thread) !== (current === 'ai')) setFilter('all');
	}, [activeHighlight?.id]);

	useEffect(() => {
		if (activeHighlight?.id) {
			const element = firstAnnotationRefs.current[activeHighlight.id];
			if (element && scrollContainerRef.current) {
				smoothScrollTo(element, scrollContainerRef.current);
			}
		}
		// `filter`: the active row only mounts once a filter hiding it gives way.
	}, [activeHighlight, filter]);

	if (threads.length === 0) {
		return (
			<div className="flex flex-col gap-4 text-center">
				<p className="text-secondary-foreground text-sm">
					There are no annotations for this paper.
				</p>
			</div>
		);
	}

	return (
		<div className="flex flex-col h-full">
			<div
				role="radiogroup"
				aria-label="Filter annotations"
				className="flex shrink-0 items-center gap-1 border-b border-border px-4 py-2"
			>
				{THREAD_FILTERS.map(({ value, label }) => {
					const selected = filter === value;
					return (
						<button
							key={value}
							type="button"
							role="radio"
							aria-checked={selected}
							onClick={() => setFilter(value)}
							className={cn(
								"flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium transition-colors",
								selected
									? "bg-foreground text-background"
									: "text-muted-foreground hover:bg-muted hover:text-foreground"
							)}
						>
							{label}
							<span className={cn("tabular-nums", selected ? "opacity-70" : "opacity-60")}>
								{filterCounts[value]}
							</span>
						</button>
					);
				})}
			</div>
			<div className="flex-1 overflow-auto" ref={scrollContainerRef}>
				{visibleThreads.length === 0 && (
					<p className="px-4 py-6 text-center text-sm text-muted-foreground">
						{filter === 'ai' ? 'No AI annotations for this paper.' : 'You have no annotations on this paper yet.'}
					</p>
				)}
				<div className="divide-y divide-border">
					{visibleThreads.map(({ highlight, annotations: threadAnns }) => {
						const hid = highlight.id!;
						const isActive = activeHighlight?.id === hid;
						const color: HighlightColor = highlight.role === 'assistant'
							? 'purple'
							: (highlight.color || 'blue');
						const bg = isActive
							? "bg-white dark:bg-zinc-950"
							: ITEM_BG_MAP[color];
						return (
							<div
								key={hid}
								data-annotation-sidebar-row=""
								data-thread-id={hid}
								ref={(el) => {
									firstAnnotationRefs.current[hid] = el;
								}}
								className={`px-4 py-3 cursor-pointer transition-colors outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 ${bg}`}
								role="group"
								tabIndex={0}
								aria-current={isActive ? true : undefined}
								aria-label={`Annotation thread: ${(highlight.raw_text ?? '').slice(0, 80)}`}
								// Activating a thread also expands it (NoteThread).
								onClick={() => onHighlightClick(highlight)}
								onKeyDown={(e) => {
									// Only the row itself; keys inside its textareas/buttons are theirs.
									if (e.target !== e.currentTarget) return;
									if (e.key === 'Enter' || e.key === ' ') {
										e.preventDefault();
										e.currentTarget.click();
									}
								}}
							>
								<NoteThread
									variant="panel"
									highlightId={hid}
									notes={threadAnns}
									user={user}
									isActive={isActive}
									otherActive={activeHighlight != null && !isActive}
									addAnnotation={readonly ? undefined : addAnnotation}
									updateAnnotation={readonly ? undefined : updateAnnotation}
									removeAnnotation={readonly ? undefined : removeAnnotation}
									header={
										<QuotedPassage
											highlight={highlight}
											color={color}
											isActive={isActive}
											renderedPosition={renderedHighlightPositions?.get(hid)}
										/>
									}
								/>
							</div>
						);
					})}
				</div>
			</div>
		</div>
	);
}

/** The highlighted PDF text heading a thread, with a note when it couldn't be placed exactly. */
function QuotedPassage({
	highlight,
	color,
	isActive,
	renderedPosition,
}: {
	highlight: PaperHighlight;
	color: HighlightColor;
	isActive: boolean;
	renderedPosition: RenderedHighlightPosition | undefined;
}) {
	if (!highlight.raw_text?.trim()) return null;
	const isAssistant = highlight.role === 'assistant';
	const hasPdfAnchor = Boolean(highlight.position || renderedPosition);
	const isUnanchoredAssistant = isAssistant && !hasPdfAnchor;
	const isApproximateAssistantAnchor =
		isAssistant &&
		!highlight.position &&
		renderedPosition?.matchStrategy &&
		renderedPosition.matchStrategy !== "normalized";
	return (
		<div className={cn("min-w-0 border-l-2 pl-3 mb-0", QUOTE_ACCENT_BORDER[color])}>
			<CollapsibleNoteText
				content={highlight.raw_text}
				isActive={isActive}
				paragraphClassName="text-xs text-muted-foreground whitespace-pre-wrap break-words"
			/>
			{isUnanchoredAssistant ? (
				<p className="mt-1 text-[11px] font-medium text-muted-foreground">
					Couldn&apos;t locate quote in PDF
				</p>
			) : isApproximateAssistantAnchor ? (
				<p className="mt-1 text-[11px] font-medium text-muted-foreground">
					Located approximately in PDF
				</p>
			) : null}
		</div>
	);
}
