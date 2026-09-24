"use client"

import { API_BASE_URL, api, errorDetail, unwrap, type Schemas } from "@/lib/api/client"
import { Button } from "@/components/ui/button"
import { Suspense, useCallback, useEffect, useRef, useState } from "react"
import useSWR from "swr"
import { useRouter, useSearchParams } from "next/navigation"
import { Search } from "lucide-react"
import { Skeleton } from "@/components/ui/skeleton"
import DiscoverHistory, { DiscoverSearchHistory } from "./DiscoverHistory"
import DiscoverInput, { DiscoverSort, SearchMode, YearFilter } from "./DiscoverInput"
import DiscoverResultCard, { DiscoverResult } from "./DiscoverResultCard"
import SubqueryList from "./SubqueryList"

const END_DELIMITER = "END_OF_STREAM"

const EXAMPLE_QUERIES = [
    "How do large language models handle long context windows?",
    "What are the environmental impacts of lithium mining?",
    "Recent advances in CRISPR gene editing therapies",
    "Neural mechanisms of decision making under uncertainty",
]

interface SubqueryResults {
    subquery: string
    results: DiscoverResult[]
}

function DiscoverPageContent() {
    const router = useRouter()
    const searchParams = useSearchParams()

    const [question, setQuestion] = useState("")
    const [submittedQuestion, setSubmittedQuestion] = useState<string | null>(null)
    const [loading, setLoading] = useState(false)
    const [subqueries, setSubqueries] = useState<string[]>([])
    const [activeSubquery, setActiveSubquery] = useState<string>("")
    const [resultGroups, setResultGroups] = useState<SubqueryResults[]>([])
    const [error, setError] = useState<string | null>(null)
    const [selectedSources, setSelectedSources] = useState<string[]>([])
    const [sort, setSort] = useState<DiscoverSort>(null)
    const [mode, setMode] = useState<SearchMode>("scholarly")
    const [onlyOpenAccess, setOnlyOpenAccess] = useState(false)
    const [yearFilter, setYearFilter] = useState<YearFilter>(null)

    // One controller for whatever is currently filling the results (a search
    // stream or a saved search being loaded). Starting another aborts it, so a
    // late chunk from an older request can't land on top of newer results.
    const requestRef = useRef<AbortController | null>(null)
    const startRequest = useCallback(() => {
        requestRef.current?.abort()
        const controller = new AbortController()
        requestRef.current = controller
        return controller
    }, [])
    useEffect(() => () => requestRef.current?.abort(), [])

    const loadSearchById = useCallback(async (id: string) => {
        const { signal } = startRequest()
        // Any running search stream was just aborted; its `finally` leaves
        // these alone because it no longer owns `requestRef`.
        setLoading(false)
        setActiveSubquery("")
        try {
            const data = await unwrap(api.GET("/api/discover/{search_id}", {
                params: { path: { search_id: id } },
                signal,
            }))
            if (signal.aborted) return
            setQuestion(data.question)
            setSubmittedQuestion(data.question)
            setSubqueries(data.subqueries || [])
            setError(null)

            const groups: SubqueryResults[] = []
            if (data.results) {
                for (const [subquery, results] of Object.entries(data.results)) {
                    groups.push({
                        subquery,
                        results,
                    })
                }
            }
            setResultGroups(groups)
        } catch {
            if (signal.aborted) return
            setError("Search not found")
        }
    }, [startRequest])

    // History and sources fail silently (the page works without them).
    const { data: history = [] } = useSWR(["/api/discover/history"], () => unwrap(api.GET("/api/discover/history")))
    const { data: sources = [] } = useSWR(["/api/discover/sources"], () => unwrap(api.GET("/api/discover/sources")))

    const handleSourceToggle = (sourceKey: string) => {
        setSelectedSources((prev) =>
            prev.includes(sourceKey)
                ? prev.filter((s) => s !== sourceKey)
                : [...prev, sourceKey]
        )
    }

    // Load search from URL ?id= param on mount
    useEffect(() => {
        const id = searchParams.get("id")
        if (id) {
            loadSearchById(id)
        }
    }, [searchParams, loadSearchById])

    const handleReset = () => {
        requestRef.current?.abort()
        setQuestion("")
        setSubmittedQuestion(null)
        setSubqueries([])
        setResultGroups([])
        setActiveSubquery("")
        setError(null)
        setSelectedSources([])
        setSort(null)
        setMode("scholarly")
        setOnlyOpenAccess(false)
        setYearFilter(null)
        router.push("/discover")
    }

    const handleSearch = async () => {
        if (!question.trim() || loading) return

        const q = question.trim()
        const controller = startRequest()
        const { signal } = controller
        setSubmittedQuestion(q)
        setLoading(true)
        setSubqueries([])
        setResultGroups([])
        setActiveSubquery("")
        setError(null)

        try {
            const requestBody: Schemas["DiscoverSearchRequest"] = {
                question: question.trim(),
                only_open_access: false, // the server default
            }

            // Set sources based on mode
            if (mode === "scholarly") {
                requestBody.sources = ["openalex"]
                if (sort) {
                    requestBody.sort = sort
                }
                if (onlyOpenAccess) {
                    requestBody.only_open_access = true
                }
            } else if (selectedSources.length > 0) {
                // Explore mode with specific domain filters
                requestBody.sources = selectedSources.filter(s => s !== "openalex")
            }
            // Explore mode with no filters = use Exa with default domains

            // Year filter applies to both modes
            if (yearFilter) {
                requestBody.year_filter = yearFilter
            }

            // SSE-style stream: raw fetch (the typed client doesn't stream).
            const response = await fetch(`${API_BASE_URL}/api/discover/search`, {
                method: "POST",
                headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
                body: JSON.stringify(requestBody),
                credentials: "include",
                signal,
            })
            if (!response.ok) {
                const body = await response.json().catch(() => undefined)
                throw new Error(errorDetail(body, response.status))
            }
            if (!response.body) throw new Error("Response body is null")

            const reader = response.body.getReader()
            const decoder = new TextDecoder()
            let buffer = ""

            while (true) {
                const { done, value } = await reader.read()
                if (done || signal.aborted) break

                buffer += decoder.decode(value, { stream: true })

                // Process complete chunks
                const chunks = buffer.split(END_DELIMITER)
                buffer = chunks.pop() || ""

                for (const chunk of chunks) {
                    if (signal.aborted) break
                    const trimmed = chunk.trim()
                    if (!trimmed) continue

                    try {
                        const parsed = JSON.parse(trimmed)

                        if (parsed.type === "subqueries") {
                            setSubqueries(parsed.content)
                        } else if (parsed.type === "results") {
                            setActiveSubquery(parsed.subquery || "")
                            setResultGroups((prev) => [
                                ...prev,
                                {
                                    subquery: parsed.subquery || "",
                                    results: parsed.content || [],
                                },
                            ])
                        } else if (parsed.type === "done") {
                            if (parsed.search_id) {
                                router.replace(`/discover?id=${parsed.search_id}`)
                            }
                        } else if (parsed.type === "error") {
                            setError(parsed.content)
                        }
                    } catch {
                        // Skip unparseable chunks
                    }
                }
            }

        } catch (err) {
            if (signal.aborted) return
            setError(err instanceof Error ? err.message : "Search failed")
        } finally {
            // Only the request that still owns the results may clear the
            // loading state; an aborted one was superseded (or unmounted).
            if (requestRef.current === controller) {
                requestRef.current = null
                setLoading(false)
                setActiveSubquery("")
            }
        }
    }

    const handleHistorySelect = (search: DiscoverSearchHistory) => {
        router.push(`/discover?id=${search.id}`)
    }

    const handleExampleClick = (example: string) => {
        requestRef.current?.abort()
        setQuestion(example)
        setSubmittedQuestion(null)
        setSubqueries([])
        setResultGroups([])
        setActiveSubquery("")
        setError(null)
        router.push("/discover")
    }

    // Normalize title for comparison (lowercase, remove punctuation, collapse whitespace)
    const normalizeTitle = (title: string) =>
        title.toLowerCase().replace(/[^\w\s]/g, "").replace(/\s+/g, " ").trim()

    // Deduplicate by URL and similar titles
    const globalSeenUrls = new Set<string>()
    const globalSeenTitles = new Set<string>()
    const dedupedGroups = resultGroups.map((group) => {
        const dedupedResults = group.results.filter((r) => {
            const url = r.url ?? ""
            if (globalSeenUrls.has(url)) return false
            const normalizedTitle = normalizeTitle(r.title ?? "")
            if (globalSeenTitles.has(normalizedTitle)) return false
            globalSeenUrls.add(url)
            globalSeenTitles.add(normalizedTitle)
            return true
        })
        return { ...group, results: dedupedResults }
    })

    const totalResults = dedupedGroups.reduce((sum, g) => sum + g.results.length, 0)

    // Subqueries that have received results (even if empty after dedup)
    const completedSubqueries = new Set(resultGroups.map((g) => g.subquery))

    const hasResults = submittedQuestion !== null

    return (
        <div className={`w-full px-4 overflow-x-hidden ${!hasResults ? "min-h-[calc(100vh-4rem)] flex flex-col items-center justify-center" : "py-6 space-y-6"}`}>
            {!hasResults ? (
                <div className="w-full space-y-6">
                    <DiscoverInput
                        value={question}
                        onChange={setQuestion}
                        onSubmit={handleSearch}
                        loading={loading}
                        sources={sources}
                        selectedSources={selectedSources}
                        onSourceToggle={handleSourceToggle}
                        sort={sort}
                        onSortChange={setSort}
                        mode={mode}
                        onModeChange={setMode}
                        onlyOpenAccess={onlyOpenAccess}
                        onOpenAccessChange={setOnlyOpenAccess}
                        yearFilter={yearFilter}
                        onYearFilterChange={setYearFilter}
                    />

                    {history.length > 0 && (
                        <div className="max-w-2xl mx-auto flex justify-center">
                            <DiscoverHistory searches={history} onSelect={handleHistorySelect} />
                        </div>
                    )}
                </div>
            ) : (
                <>
                    {/* Results header */}
                    <div className="max-w-2xl mx-auto">
                        <h1 className="text-xl font-semibold">{submittedQuestion}</h1>
                    </div>

                    {(subqueries.length > 0 || loading) && (
                        <div className="max-w-2xl mx-auto">
                            <SubqueryList
                                subqueries={subqueries}
                                loading={loading && subqueries.length === 0}
                                activeSubquery={activeSubquery}
                                completedSubqueries={completedSubqueries}
                            />
                        </div>
                    )}

                    {error && (
                        <div className="max-w-2xl mx-auto text-sm text-destructive bg-destructive/10 rounded-md p-3">
                            {error}
                        </div>
                    )}

                    {/* Results grouped by subquery */}
                    <div className="max-w-2xl mx-auto space-y-10">
                        {loading && resultGroups.length === 0 && subqueries.length > 0 && (
                            <div className="space-y-4">
                                {[...Array(4)].map((_, i) => (
                                    <div key={i} className="py-4 border-b border-slate-200 dark:border-slate-800">
                                        <Skeleton className="h-5 w-3/4 mb-2" />
                                        <Skeleton className="h-3 w-1/3 mb-2" />
                                        <Skeleton className="h-4 w-full mb-1" />
                                        <Skeleton className="h-4 w-2/3" />
                                    </div>
                                ))}
                            </div>
                        )}

                        {dedupedGroups.map((group) => {
                            if (group.results.length === 0) return null
                            return (
                                <div key={group.subquery}>
                                    <div className="bg-slate-100 dark:bg-slate-800/50 rounded-md px-3 py-2 mb-2">
                                        <h3 className="text-sm font-medium text-slate-700 dark:text-slate-300">
                                            {group.subquery}
                                        </h3>
                                    </div>
                                    <div>
                                        {group.results.map((result, idx) => (
                                            <DiscoverResultCard
                                                key={`${result.url}-${idx}`}
                                                result={result}
                                            />
                                        ))}
                                    </div>
                                </div>
                            )
                        })}

                        {!loading && totalResults === 0 && subqueries.length > 0 && (
                            <div className="text-center py-8 space-y-4">
                                <p className="text-muted-foreground">
                                    No results found{mode === "scholarly" ? " in academic databases" : ""}. Try a different query or explore these examples:
                                </p>
                                <div className="flex flex-wrap justify-center gap-2">
                                    {EXAMPLE_QUERIES.map((example) => (
                                        <button
                                            key={example}
                                            onClick={() => handleExampleClick(example)}
                                            className="text-sm px-3 py-1.5 rounded-full border border-slate-200 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors text-left"
                                        >
                                            {example}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Ask another question */}
                    {!loading && (
                        <div className="fixed bottom-6 right-6">
                            <Button onClick={handleReset} className="gap-2 shadow-lg">
                                <Search className="h-4 w-4" />
                                Find more literature
                            </Button>
                        </div>
                    )}
                </>
            )}
        </div>
    )
}

export default function DiscoverPage() {
    return (
        <Suspense fallback={<div className="w-full px-4 py-6">Loading...</div>}>
            <DiscoverPageContent />
        </Suspense>
    )
}
