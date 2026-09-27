import { Skeleton } from "@/components/ui/skeleton";

export default function ProjectPageSkeleton() {
    return (
        <div className="mx-auto w-full max-w-6xl px-4 py-5 sm:px-6 sm:py-8 lg:px-8">
            {/* Back link + header */}
            <div className="mb-6 sm:mb-8">
                <Skeleton className="mb-3 h-5 w-20" />
                <Skeleton className="mb-2 h-8 w-64 max-w-full" />
                <Skeleton className="h-5 w-96 max-w-full" />
            </div>

            {/* Papers */}
            <div className="space-y-4">
                <div className="flex items-center justify-between">
                    <Skeleton className="h-6 w-24" />
                    <Skeleton className="h-9 w-20 rounded-md" />
                </div>
                <div className="grid grid-cols-1 gap-3 sm:gap-4">
                    <Skeleton className="h-24 w-full rounded-xl" />
                    <Skeleton className="h-24 w-full rounded-xl" />
                    <Skeleton className="h-24 w-full rounded-xl" />
                </div>
            </div>
        </div>
    );
}
