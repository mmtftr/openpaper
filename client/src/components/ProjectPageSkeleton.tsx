import { Skeleton } from "@/components/ui/skeleton";

export default function ProjectPageSkeleton() {
    return (
        <div className="container mx-auto p-4">
            {/* Breadcrumb Skeleton */}
            <div className="mb-4">
                <Skeleton className="h-4 w-48" />
            </div>

            {/* Project Header Skeleton */}
            <div className="group relative mb-6">
                <div className="flex items-start justify-between gap-4">
                    <div className="flex-1">
                        <Skeleton className="h-9 w-64 mb-2" />
                        <Skeleton className="h-6 w-96 max-w-full" />
                    </div>
                </div>
            </div>

            <div className="flex flex-col lg:flex-row gap-6 -mx-4">
                {/* Papers Skeleton */}
                <div className="w-full px-4 space-y-4">
                    <div className="flex justify-between items-center mb-4">
                        <Skeleton className="h-8 w-20" />
                        <Skeleton className="h-9 w-20 rounded-md" />
                    </div>
                    <div className="grid grid-cols-1 gap-4">
                        <Skeleton className="h-24 w-full rounded-lg" />
                        <Skeleton className="h-24 w-full rounded-lg" />
                        <Skeleton className="h-24 w-full rounded-lg" />
                    </div>
                </div>
            </div>
        </div>
    );
}
