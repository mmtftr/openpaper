import { Skeleton } from "@/components/ui/skeleton";

/** Stand-in for the paper page while it loads: the split on desktop, one pane on phones. */
export default function PaperViewSkeleton() {
    return (
        <div className="flex min-h-0 w-full flex-1 flex-col md:flex-row">
            <div className="flex min-h-0 flex-1 flex-col md:w-3/5 md:flex-none md:border-r">
                <div className="flex h-11 shrink-0 items-center gap-2 border-b px-3">
                    <Skeleton className="h-6 w-6" />
                    <Skeleton className="h-6 w-16" />
                    <Skeleton className="mx-auto h-6 w-24" />
                </div>
                <div className="min-h-0 flex-1 bg-muted p-4 md:p-6">
                    <Skeleton className="mx-auto h-full max-w-3xl rounded-sm bg-background/80" />
                </div>
            </div>
            <div className="hidden w-2/5 flex-col gap-4 p-4 md:flex">
                <Skeleton className="h-8 w-40" />
                <Skeleton className="h-24 w-full" />
                <Skeleton className="h-40 w-full" />
                <div className="flex-1" />
                <Skeleton className="h-24 w-full rounded-xl" />
            </div>
            <div className="h-(--app-tabbar-h) shrink-0 border-t md:hidden" />
        </div>
    );
}
