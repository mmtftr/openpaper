"use client"

import { api, unwrap } from "@/lib/api/client"
import useSWR from "swr"
import { useRouter } from "next/navigation"
import { ArrowLeft } from "lucide-react"
import Link from "next/link"
import { DiscoverSearchHistory } from "../DiscoverHistory"

export default function DiscoverHistoryPage() {
    const router = useRouter()
    // Fails silently (an empty history)
    const { data: searches = [], isLoading: loading } = useSWR(
        ["/api/discover/history"],
        () => unwrap(api.GET("/api/discover/history")),
    )

    const handleSelect = (search: DiscoverSearchHistory) => {
        router.push(`/discover?id=${search.id}`)
    }

    return (
        <div className="mx-auto w-full max-w-2xl animate-rise-in px-4 py-6">
            <div className="mb-6">
                <Link
                    href="/discover"
                    className="-ml-2 inline-flex h-9 items-center gap-1.5 rounded-md px-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
                >
                    <ArrowLeft className="h-4 w-4" />
                    Back to Discover
                </Link>
            </div>

            <h1 className="text-xl font-semibold mb-6">Search History</h1>

            {loading ? (
                <div className="text-sm text-muted-foreground">Loading...</div>
            ) : searches.length === 0 ? (
                <div className="text-sm text-muted-foreground">No searches yet.</div>
            ) : (
                <div className="space-y-1">
                    {searches.map((search) => (
                        <button
                            key={search.id}
                            onClick={() => handleSelect(search)}
                            className="w-full rounded-lg px-3 py-3 text-left transition-colors hover:bg-accent"
                        >
                            <div className="text-sm font-medium">{search.question}</div>
                            <div className="text-xs text-muted-foreground mt-1">
                                {search.created_at
                                    ? new Date(search.created_at).toLocaleDateString(undefined, {
                                          year: "numeric",
                                          month: "short",
                                          day: "numeric",
                                      })
                                    : ""}
                                {" · "}
                                {search.subqueries?.length || 0} subqueries
                                {" · "}
                                {Object.values(search.results || {}).flat().length} results
                            </div>
                        </button>
                    ))}
                </div>
            )}
        </div>
    )
}
