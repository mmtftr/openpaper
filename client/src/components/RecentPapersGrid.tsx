"use client";

import Link from "next/link";
import Image from "next/image";
import { ArrowRight, FileText } from "lucide-react";
import useSWR from "swr";
import { api, unwrap, type Schemas } from "@/lib/api/client";
import { Skeleton } from "@/components/ui/skeleton";
import { cn, formatDate } from "@/lib/utils";

type RelevantPaper = Schemas["RelevantPaper"];

interface RecentPapersGridProps {
    papers?: RelevantPaper[];
    limit?: number;
}

// Phones: a compact row with a small thumbnail (titles need the width).
// From `sm`: a card with the first-page preview on top.
function PaperCardCompact({ paper }: { paper: RelevantPaper }) {
    const createdAt = paper.created_at ? formatDate(paper.created_at) : null;
    const firstAuthor = paper.authors?.[0];

    return (
        <Link
            href={`/paper/${paper.id}`}
            className={cn(
                "group flex h-full items-center gap-3 p-3",
                "transition-[background-color,border-color,box-shadow,transform] duration-200 ease-out-soft",
                "active:bg-accent/60 sm:active:bg-card",
                "sm:flex-col sm:items-stretch sm:gap-0 sm:overflow-hidden sm:rounded-xl sm:border sm:border-border/60 sm:bg-card sm:p-0",
                "sm:hover:-translate-y-0.5 sm:hover:border-border sm:hover:shadow-md",
                "focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-brand/30 sm:focus-visible:rounded-xl"
            )}
        >
            <div className="relative aspect-[3/4] w-12 shrink-0 overflow-hidden rounded-md border border-border/60 bg-muted sm:aspect-[4/3] sm:w-full sm:rounded-none sm:border-0 sm:border-b">
                {paper.preview_url ? (
                    <Image
                        src={paper.preview_url}
                        alt=""
                        className="object-cover object-top transition-transform duration-500 ease-out-soft group-hover:scale-[1.03] sm:object-center"
                        fill
                        unoptimized
                    />
                ) : (
                    <div className="flex h-full items-center justify-center text-brand/70">
                        <FileText className="size-5 sm:size-8" />
                    </div>
                )}
            </div>

            <div className="flex min-w-0 flex-1 flex-col sm:p-4">
                <h3 className="line-clamp-2 text-sm font-medium leading-snug transition-colors sm:flex-1 sm:group-hover:text-brand">
                    {paper.title || "Untitled Paper"}
                </h3>
                <p className="mt-1 flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground sm:mt-3">
                    {firstAuthor && (
                        <span className="truncate">
                            {firstAuthor}
                            {paper.authors!.length > 1 && ` +${paper.authors!.length - 1}`}
                        </span>
                    )}
                    {firstAuthor && createdAt && <span aria-hidden>·</span>}
                    {createdAt && <span className="shrink-0 tabular-nums">{createdAt}</span>}
                </p>
            </div>
        </Link>
    );
}

function PaperCardSkeleton() {
    return (
        <div className="flex items-center gap-3 p-3 sm:flex-col sm:items-stretch sm:gap-0 sm:overflow-hidden sm:rounded-xl sm:border sm:border-border/60 sm:bg-card sm:p-0">
            <Skeleton className="aspect-[3/4] w-12 shrink-0 rounded-md sm:aspect-[4/3] sm:w-full sm:rounded-none" />
            <div className="flex-1 space-y-2 sm:p-4">
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-3 w-1/2" />
            </div>
        </div>
    );
}

const LIST_CLASSES = cn(
    "divide-y divide-border/60 overflow-hidden rounded-xl border border-border/60 bg-card",
    "sm:grid sm:grid-cols-2 sm:gap-4 sm:divide-y-0 sm:overflow-visible sm:rounded-none sm:border-0 sm:bg-transparent lg:grid-cols-3 xl:grid-cols-4"
);

export function RecentPapersGrid({ papers: propPapers, limit = 6 }: RecentPapersGridProps) {
    // Without papers from props, load the most relevant ones.
    const { data, isLoading } = useSWR(
        propPapers ? null : ["/api/paper/relevant"],
        () => unwrap(api.GET("/api/paper/relevant")),
        { onError: (error) => console.error("Error fetching papers:", error) },
    );
    const papers = (propPapers ?? data?.papers ?? []).slice(0, limit);

    if (isLoading) {
        return (
            <div className="space-y-4">
                <div className="flex items-center justify-between">
                    <Skeleton className="h-6 w-32" />
                    <Skeleton className="h-4 w-24" />
                </div>
                <div className={LIST_CLASSES}>
                    {[...Array(limit)].map((_, i) => (
                        <PaperCardSkeleton key={i} />
                    ))}
                </div>
            </div>
        );
    }

    if (papers.length === 0) {
        return null; // Don't show section if no papers
    }

    return (
        <div className="space-y-3 sm:space-y-4">
            <div className="flex items-center justify-between">
                <h2 className="text-base font-semibold sm:text-lg">Recent papers</h2>
                <Link
                    href="/papers"
                    className="-mr-2 flex h-9 items-center gap-1 rounded-md px-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
                >
                    View library
                    <ArrowRight className="h-3.5 w-3.5" />
                </Link>
            </div>

            <div className={LIST_CLASSES}>
                {papers.map((paper, i) => (
                    // The entrance lives on a wrapper: its fill would pin the
                    // card's transform and cancel the hover lift.
                    <div key={paper.id} className="animate-rise-in" style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}>
                        <PaperCardCompact paper={paper} />
                    </div>
                ))}
            </div>
        </div>
    );
}
