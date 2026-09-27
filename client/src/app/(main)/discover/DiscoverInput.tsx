"use client"

import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "@/components/ui/popover"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"
import { ArrowDownNarrowWide, Calendar, Check, ChevronDown, Search } from "lucide-react"
import { useRef } from "react"
import { LayoutGroup, motion } from "motion/react"
import { PILL_SPRING } from "@/lib/motion"
import type { Schemas } from "@/lib/api/client"

export type DiscoverSource = Schemas["DiscoverSource"]

export type SearchMode = "scholarly" | "explore"
export type DiscoverSort = "cited_by_count:desc" | "publication_date:desc" | null
export type YearFilter = "last_year" | "last_5_years" | null

const SORT_OPTIONS: { value: DiscoverSort; label: string }[] = [
    { value: null, label: "Relevance" },
    { value: "cited_by_count:desc", label: "Most cited" },
    { value: "publication_date:desc", label: "Newest" },
]

const PLACEHOLDERS: Record<SearchMode, string> = {
    scholarly: "Search structured indexed academic databases...",
    explore: "Search across preprints, journals, and research sites...",
}

const YEAR_FILTER_OPTIONS: { value: YearFilter; label: string }[] = [
    { value: null, label: "All time" },
    { value: "last_year", label: "Last year" },
    { value: "last_5_years", label: "Last 5 years" },
]

interface DiscoverInputProps {
    value: string
    onChange: (value: string) => void
    onSubmit: () => void
    loading: boolean
    sources: DiscoverSource[]
    selectedSources: string[]
    onSourceToggle: (sourceKey: string) => void
    sort: DiscoverSort
    onSortChange: (sort: DiscoverSort) => void
    mode: SearchMode
    onModeChange: (mode: SearchMode) => void
    onlyOpenAccess: boolean
    onOpenAccessChange: (value: boolean) => void
    yearFilter: YearFilter
    onYearFilterChange: (filter: YearFilter) => void
}

export default function DiscoverInput({
    value,
    onChange,
    onSubmit,
    loading,
    sources,
    selectedSources,
    onSourceToggle,
    sort,
    onSortChange,
    mode,
    onModeChange,
    onlyOpenAccess,
    onOpenAccessChange,
    yearFilter,
    onYearFilterChange,
}: DiscoverInputProps) {
    const textareaRef = useRef<HTMLTextAreaElement>(null)

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault()
            if (value.trim() && !loading) {
                onSubmit()
            }
        }
    }

    // Filter out openalex from sources (it's now handled by mode)
    const webSources = sources.filter(s => s.key !== "openalex")
    const selectedWebSources = selectedSources.filter(s => s !== "openalex")
    const selectedCount = selectedWebSources.length

    const sourcesLabel = selectedCount === 0
        ? "All sources"
        : selectedCount === 1
            ? webSources.find(s => s.key === selectedWebSources[0])?.label || "1 source"
            : `${selectedCount} sources`

    const currentSortLabel = SORT_OPTIONS.find(o => o.value === sort)?.label || "Relevance"
    const currentYearFilterLabel = YEAR_FILTER_OPTIONS.find(o => o.value === yearFilter)?.label || "All time"

    const chip = cn(
        "flex h-10 items-center gap-1.5 rounded-md px-2.5 text-sm transition-colors sm:h-8 sm:px-2",
        "hover:bg-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    )

    return (
        <div className="mx-auto w-full max-w-2xl space-y-4">
            <div className="space-y-1.5 text-center">
                <h1 className="text-2xl font-semibold tracking-tight">Discover Research</h1>
                <p className="text-sm text-muted-foreground">
                    Enter a research question and we&apos;ll find relevant papers across the web.
                </p>
            </div>
            <div className="rounded-xl border bg-background shadow-xs transition-[border-color,box-shadow] duration-200 ease-out-soft focus-within:border-brand/50 focus-within:ring-4 focus-within:ring-brand/15 dark:bg-input/30">
                <Textarea
                    ref={textareaRef}
                    aria-label="Research question"
                    placeholder={PLACEHOLDERS[mode]}
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                    onKeyDown={handleKeyDown}
                    enterKeyHint="search"
                    // Leaves room for the Search button when the phone
                    // keyboard scrolls the field into view.
                    className="min-h-[88px] scroll-mb-28 resize-none rounded-xl border-0 bg-transparent px-3.5 pt-3 shadow-none focus-visible:ring-0 dark:bg-transparent"
                    rows={3}
                />

                {/* Controls: in flow under the text so they can wrap on phones */}
                <div className="flex flex-col gap-2 p-2 pt-0 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex flex-wrap items-center gap-1">
                        {/* Mode toggle */}
                        <LayoutGroup id="discover-mode">
                            <div className="mr-1 flex items-center rounded-lg bg-muted p-0.5" role="group" aria-label="Search mode">
                                {(["scholarly", "explore"] as const).map((m) => (
                                    <button
                                        key={m}
                                        type="button"
                                        onClick={() => onModeChange(m)}
                                        aria-pressed={mode === m}
                                        className={cn(
                                            "relative isolate h-9 rounded-md px-3 text-sm capitalize transition-colors sm:h-7 sm:px-2.5",
                                            mode === m ? "text-foreground" : "text-muted-foreground hover:text-foreground"
                                        )}
                                    >
                                        {mode === m && (
                                            <motion.span
                                                layoutId="mode-pill"
                                                transition={PILL_SPRING}
                                                className="absolute inset-0 -z-10 rounded-md bg-background shadow-sm dark:bg-accent"
                                            />
                                        )}
                                        {m}
                                    </button>
                                ))}
                            </div>
                        </LayoutGroup>

                        {/* Academic mode: sort dropdown and open access filter */}
                        {mode === "scholarly" && (
                            <>
                                <Popover>
                                    <PopoverTrigger asChild>
                                        <button
                                            type="button"
                                            className={cn(chip, sort ? "text-foreground" : "text-muted-foreground")}
                                        >
                                            <ArrowDownNarrowWide className="h-3.5 w-3.5" />
                                            {currentSortLabel}
                                            <ChevronDown className="h-3.5 w-3.5" />
                                        </button>
                                    </PopoverTrigger>
                                    <PopoverContent className="w-40 p-1" align="start">
                                        <div className="space-y-0.5">
                                            {SORT_OPTIONS.map((option) => (
                                                <button
                                                    key={option.label}
                                                    type="button"
                                                    onClick={() => onSortChange(option.value)}
                                                    className={cn(
                                                        "w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                                                        "hover:bg-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                                                        sort === option.value && "bg-accent"
                                                    )}
                                                >
                                                    <Check className={cn(
                                                        "h-3.5 w-3.5",
                                                        sort === option.value ? "opacity-100" : "opacity-0"
                                                    )} />
                                                    {option.label}
                                                </button>
                                            ))}
                                        </div>
                                    </PopoverContent>
                                </Popover>

                                <label className={cn(chip, "cursor-pointer whitespace-nowrap")}>
                                    <Checkbox
                                        checked={onlyOpenAccess}
                                        onCheckedChange={(checked) => onOpenAccessChange(checked === true)}
                                        className="h-3.5 w-3.5"
                                    />
                                    <span className={onlyOpenAccess ? "text-foreground" : "text-muted-foreground"}>
                                        Open Access
                                    </span>
                                </label>
                            </>
                        )}

                        {/* Explore mode: domain filter dropdown */}
                        {mode === "explore" && webSources.length > 0 && (
                            <Popover>
                                <PopoverTrigger asChild>
                                    <button
                                        type="button"
                                        className={cn(chip, selectedCount > 0 ? "text-foreground" : "text-muted-foreground")}
                                    >
                                        {sourcesLabel}
                                        <ChevronDown className="h-3.5 w-3.5" />
                                    </button>
                                </PopoverTrigger>
                                <PopoverContent className="w-64 p-2" align="start" side="bottom" avoidCollisions={false}>
                                    <div className="space-y-1 max-h-64 overflow-y-auto">
                                        {webSources.map((source) => {
                                            const isSelected = selectedSources.includes(source.key)
                                            return (
                                                <label
                                                    key={source.key}
                                                    className="flex items-start gap-3 rounded-md px-2 py-2 cursor-pointer hover:bg-accent transition-colors"
                                                >
                                                    <Checkbox
                                                        checked={isSelected}
                                                        onCheckedChange={() => onSourceToggle(source.key)}
                                                        className="mt-0.5"
                                                    />
                                                    <div className="flex-1 min-w-0">
                                                        <div className="text-sm font-medium">{source.label}</div>
                                                        <div className="text-xs text-muted-foreground">{source.description}</div>
                                                    </div>
                                                </label>
                                            )
                                        })}
                                    </div>
                                </PopoverContent>
                            </Popover>
                        )}

                        {/* Time filter (shown for both modes) */}
                        <Popover>
                            <PopoverTrigger asChild>
                                <button
                                    type="button"
                                    className={cn(chip, yearFilter ? "text-foreground" : "text-muted-foreground")}
                                >
                                    <Calendar className="h-3.5 w-3.5" />
                                    {currentYearFilterLabel}
                                    <ChevronDown className="h-3.5 w-3.5" />
                                </button>
                            </PopoverTrigger>
                            <PopoverContent className="w-40 p-1" align="start">
                                <div className="space-y-0.5">
                                    {YEAR_FILTER_OPTIONS.map((option) => (
                                        <button
                                            key={option.label}
                                            type="button"
                                            onClick={() => onYearFilterChange(option.value)}
                                            className={cn(
                                                "w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors",
                                                "hover:bg-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                                                yearFilter === option.value && "bg-accent"
                                            )}
                                        >
                                            <Check className={cn(
                                                "h-3.5 w-3.5",
                                                yearFilter === option.value ? "opacity-100" : "opacity-0"
                                            )} />
                                            {option.label}
                                        </button>
                                    ))}
                                </div>
                            </PopoverContent>
                        </Popover>
                    </div>

                    <Button
                        onClick={onSubmit}
                        disabled={!value.trim() || loading}
                        className="h-10 w-full shrink-0 gap-2 sm:h-8 sm:w-auto"
                    >
                        <Search className="h-4 w-4" />
                        Search
                    </Button>
                </div>
            </div>
        </div>
    )
}
