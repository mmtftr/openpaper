"use client";

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import { Search, FileText, FolderKanban, Command, Loader2, Highlighter, ChevronDown, ChevronUp } from "lucide-react";
import { Input } from "@/components/ui/input";
import { api, unwrap, type Schemas } from "@/lib/api/client";
import { useProjects } from "@/hooks/useProjects";

// Helper to check if text contains search term
const textMatchesSearch = (text: string | null | undefined, searchTerm: string): boolean => {
    if (!text) return false;
    return text.toLowerCase().includes(searchTerm.toLowerCase());
};

// Helper to highlight search terms in text
const highlightSearchTerm = (text: string, searchTerm: string): React.ReactNode => {
    if (!searchTerm || !text) return text;
    const regex = new RegExp(`(${searchTerm.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
    const parts = text.split(regex);
    return parts.map((part, index) =>
        regex.test(part) ? (
            <mark key={index} className="bg-yellow-200 dark:bg-yellow-800 px-0.5 rounded">
                {part}
            </mark>
        ) : part
    );
};

// Represents a selectable item in the search results
type SelectableItem =
    | { type: "project"; id: string }
    | { type: "paper"; id: string }
    | { type: "ask" };

export function HomeSearch() {
    const [query, setQuery] = useState("");
    const [isOpen, setIsOpen] = useState(false);
    const [isLoading, setIsLoading] = useState(false);
    const [hasSearched, setHasSearched] = useState(false);
    const [papers, setPapers] = useState<Schemas["PaperResult"][]>([]);
    const [selectedIndex, setSelectedIndex] = useState(0);
    const [expandedPaperId, setExpandedPaperId] = useState<string | null>(null);
    const { projects: allProjects } = useProjects();
    const inputRef = useRef<HTMLInputElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const resultsRef = useRef<HTMLDivElement>(null);
    const abortControllerRef = useRef<AbortController | null>(null);
    const router = useRouter();

    // Filter projects client-side based on query
    const filteredProjects = useMemo(() => {
        if (!query.trim()) return [];
        const lowerQuery = query.toLowerCase();
        return allProjects
            .filter((p) =>
                p.title?.toLowerCase().includes(lowerQuery) ||
                p.description?.toLowerCase().includes(lowerQuery)
            )
            .slice(0, 3);
    }, [allProjects, query]);

    // Build a flat list of selectable items for keyboard navigation
    const selectableItems = useMemo((): SelectableItem[] => {
        const items: SelectableItem[] = [];
        filteredProjects.forEach((p) => items.push({ type: "project", id: p.id }));
        papers.forEach((p) => items.push({ type: "paper", id: p.id }));
        return items;
    }, [filteredProjects, papers]);

    const hasResults = papers.length > 0 || filteredProjects.length > 0;

    // Reset selected index when results change
    useEffect(() => {
        setSelectedIndex(0);
    }, [selectableItems.length]);

    // Scroll selected item into view
    useEffect(() => {
        if (resultsRef.current && selectedIndex >= 0) {
            const selectedElement = resultsRef.current.querySelector(`[data-index="${selectedIndex}"]`);
            selectedElement?.scrollIntoView({ block: "nearest" });
        }
    }, [selectedIndex]);

    // Keyboard shortcut (Cmd+K) and arrow navigation
    useEffect(() => {
        const handleKeyDown = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "k") {
                e.preventDefault();
                inputRef.current?.focus();
                setIsOpen(true);
            }
            if (e.key === "Escape") {
                setIsOpen(false);
                inputRef.current?.blur();
            }
        };

        document.addEventListener("keydown", handleKeyDown);
        return () => document.removeEventListener("keydown", handleKeyDown);
    }, []);

    // Click outside to close
    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
                setIsOpen(false);
            }
        };

        document.addEventListener("mousedown", handleClickOutside);
        return () => document.removeEventListener("mousedown", handleClickOutside);
    }, []);

    // Debounced search
    useEffect(() => {
        // Cancel any pending request when query changes
        if (abortControllerRef.current) {
            abortControllerRef.current.abort();
            abortControllerRef.current = null;
        }

        if (!query.trim()) {
            setPapers([]);
            setHasSearched(false);
            setIsLoading(false);
            return;
        }

        // Reset hasSearched when query changes (user is typing)
        setHasSearched(false);
        setIsLoading(true);

        const timeout = setTimeout(async () => {
            // Create a new AbortController for this request
            const controller = new AbortController();
            abortControllerRef.current = controller;

            try {
                // Search papers
                const searchResponse = await unwrap(api.GET("/api/search/local/", {
                    params: { query: { q: query, limit: 5 } },
                    signal: controller.signal,
                }));

                // Check if this request was aborted
                if (controller.signal.aborted) return;

                // Keep full PaperResult to access highlights and annotations
                setPapers(searchResponse?.papers || []);
                setExpandedPaperId(null);
                setHasSearched(true);
                setIsLoading(false);
            } catch (error) {
                // Ignore abort errors
                if (error instanceof Error && error.name === 'AbortError') {
                    return;
                }
                console.error("Search error:", error);
                setHasSearched(true);
                setIsLoading(false);
            }
        }, 300);

        return () => {
            clearTimeout(timeout);
            // Also abort any in-flight request on cleanup
            if (abortControllerRef.current) {
                abortControllerRef.current.abort();
                abortControllerRef.current = null;
            }
        };
    }, [query]);

    const handleSelect = useCallback((type: "paper" | "project", id: string) => {
        setIsOpen(false);
        setQuery("");
        if (type === "paper") {
            router.push(`/paper/${id}`);
        } else {
            router.push(`/projects/${id}`);
        }
    }, [router]);

    const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
        if (!isOpen || selectableItems.length === 0) return;

        switch (e.key) {
            case "ArrowDown":
                e.preventDefault();
                setSelectedIndex((prev) =>
                    prev < selectableItems.length - 1 ? prev + 1 : 0
                );
                break;
            case "ArrowUp":
                e.preventDefault();
                setSelectedIndex((prev) =>
                    prev > 0 ? prev - 1 : selectableItems.length - 1
                );
                break;
            case "Enter":
                e.preventDefault();
                const selected = selectableItems[selectedIndex];
                if (selected && selected.type !== "ask") {
                    handleSelect(selected.type, selected.id);
                }
                break;
        }
    }, [isOpen, selectableItems, selectedIndex, handleSelect]);

    return (
        <div ref={containerRef} className="relative mx-auto w-full max-w-2xl">
            <div className="relative">
                <Search className="pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground" />
                <Input
                    ref={inputRef}
                    type="search"
                    aria-label="Search papers, projects and highlights"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onFocus={() => setIsOpen(true)}
                    onKeyDown={handleKeyDown}
                    className="h-12 w-full rounded-xl border border-input bg-background pl-12 pr-4 text-base shadow-xs md:text-base transition-[border-color,box-shadow] duration-200 ease-out-soft focus-visible:border-brand/50 focus-visible:ring-4 focus-visible:ring-brand/15 sm:pr-16 [&::-webkit-search-cancel-button]:hidden"
                />
                {/* The placeholder differs by width, which the attribute can't do. */}
                {!query && (
                    <span aria-hidden className="pointer-events-none absolute inset-y-0 left-12 right-4 flex items-center truncate text-base text-muted-foreground sm:right-16">
                        <span className="truncate sm:hidden">Search your library</span>
                        <span className="hidden truncate sm:inline">Search papers, projects and highlights…</span>
                    </span>
                )}
                <div className="pointer-events-none absolute right-3 top-1/2 flex -translate-y-1/2 items-center gap-1 text-xs text-muted-foreground">
                    <kbd className="hidden h-6 items-center gap-1 rounded border bg-background px-2 font-mono text-xs sm:inline-flex">
                        <Command className="h-3 w-3" />K
                    </kbd>
                </div>
            </div>

            {/* Search Results Dropdown */}
            {isOpen && query.trim() && (
                <div className="absolute inset-x-0 top-full z-50 mt-2 overflow-hidden rounded-xl border bg-popover shadow-lg animate-in fade-in-0 slide-in-from-top-1 duration-150">
                    {isLoading ? (
                        <div className="flex items-center gap-2 px-4 py-3 text-muted-foreground">
                            <Loader2 className="h-4 w-4 animate-spin" />
                            <span className="text-sm">Searching...</span>
                        </div>
                    ) : hasResults ? (
                        <div ref={resultsRef} className="max-h-[400px] overflow-y-auto">
                            {filteredProjects.length > 0 && (
                                <div className="p-2">
                                    <p className="px-3 py-2 text-xs font-medium text-muted-foreground uppercase tracking-wide">
                                        Projects
                                    </p>
                                    {filteredProjects.map((project, idx) => (
                                        <button
                                            key={project.id}
                                            data-index={idx}
                                            onClick={() => handleSelect("project", project.id)}
                                            onMouseEnter={() => setSelectedIndex(idx)}
                                            className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left transition-colors ${selectedIndex === idx ? "bg-accent" : "hover:bg-accent"}`}
                                        >
                                            <FolderKanban className="h-4 w-4 text-brand flex-shrink-0" />
                                            <div className="min-w-0">
                                                <p className="font-medium truncate">{project.title}</p>
                                                {project.description && (
                                                    <p className="text-sm text-muted-foreground truncate">
                                                        {project.description}
                                                    </p>
                                                )}
                                            </div>
                                        </button>
                                    ))}
                                </div>
                            )}
                            {papers.length > 0 && (
                                <div className="p-2 border-t">
                                    <p className="px-3 py-2 text-xs font-medium text-muted-foreground uppercase tracking-wide">
                                        Papers
                                    </p>
                                    {papers.map((paper, idx) => {
                                        const itemIndex = filteredProjects.length + idx;
                                        // Find matching highlights
                                        const matchingHighlights = paper.highlights?.filter(h =>
                                            textMatchesSearch(h.raw_text, query)
                                        ) || [];
                                        const hasMatches = matchingHighlights.length > 0;
                                        const isExpanded = expandedPaperId === paper.id;

                                        return (
                                            <div key={paper.id} className="mb-1">
                                                <button
                                                    data-index={itemIndex}
                                                    onClick={() => handleSelect("paper", paper.id)}
                                                    onMouseEnter={() => setSelectedIndex(itemIndex)}
                                                    className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left transition-colors ${selectedIndex === itemIndex ? "bg-accent" : "hover:bg-accent"}`}
                                                >
                                                    <FileText className="h-4 w-4 text-brand flex-shrink-0" />
                                                    <div className="min-w-0 flex-1">
                                                        <p className="font-medium truncate">{paper.title || "Untitled Paper"}</p>
                                                        {paper.authors && paper.authors.length > 0 && (
                                                            <p className="text-sm text-muted-foreground truncate">
                                                                {paper.authors.slice(0, 2).join(", ")}
                                                                {paper.authors.length > 2 && " et al."}
                                                            </p>
                                                        )}
                                                        {/* Match indicator */}
                                                        {hasMatches && (
                                                            <div className="flex items-center gap-2 mt-1">
                                                                <span className="text-xs text-yellow-600 dark:text-yellow-400 flex items-center gap-1">
                                                                    <Search className="h-3 w-3" />
                                                                    <span>{matchingHighlights.length} highlight{matchingHighlights.length !== 1 ? 's' : ''}</span>
                                                                </span>
                                                            </div>
                                                        )}
                                                    </div>
                                                    {/* Expand/collapse button for matches */}
                                                    {hasMatches && (
                                                        <button
                                                            onClick={(e) => {
                                                                e.stopPropagation();
                                                                setExpandedPaperId(isExpanded ? null : paper.id);
                                                            }}
                                                            className="p-1 hover:bg-accent rounded"
                                                        >
                                                            {isExpanded ? (
                                                                <ChevronUp className="h-4 w-4 text-muted-foreground" />
                                                            ) : (
                                                                <ChevronDown className="h-4 w-4 text-muted-foreground" />
                                                            )}
                                                        </button>
                                                    )}
                                                </button>

                                                {/* Expanded matching highlights */}
                                                {hasMatches && isExpanded && (
                                                    <div className="ml-10 mr-3 mb-2 space-y-2">
                                                        {matchingHighlights.slice(0, 3).map((highlight) => (
                                                            <button
                                                                key={highlight.id}
                                                                onClick={(e) => {
                                                                    e.stopPropagation();
                                                                    setIsOpen(false);
                                                                    setQuery("");
                                                                    router.push(`/paper/${paper.id}?rsf=annotations`);
                                                                }}
                                                                className="w-full p-2 text-sm border-l-2 border-yellow-400 bg-yellow-50/50 dark:bg-yellow-950/20 rounded-r text-left hover:bg-yellow-100 dark:hover:bg-yellow-900/30 transition-colors"
                                                            >
                                                                <div className="flex items-start gap-2">
                                                                    <Highlighter className="h-3 w-3 text-yellow-600 mt-0.5 flex-shrink-0" />
                                                                    <p className="line-clamp-2">
                                                                        {highlightSearchTerm(highlight.raw_text, query)}
                                                                    </p>
                                                                </div>
                                                                {highlight.page_number && (
                                                                    <p className="text-xs text-muted-foreground mt-1 ml-5">
                                                                        Page {highlight.page_number}
                                                                    </p>
                                                                )}
                                                            </button>
                                                        ))}
                                                        {matchingHighlights.length > 3 && (
                                                            <p className="text-xs text-muted-foreground ml-5">
                                                                +{matchingHighlights.length - 3} more
                                                            </p>
                                                        )}
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    ) : hasSearched ? (
                        <div className="py-6 px-4">
                            <p className="text-center text-muted-foreground">
                                No results found for &quot;{query}&quot;
                            </p>
                        </div>
                    ) : null}
                </div>
            )}
        </div>
    );
}
