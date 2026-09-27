"use client"

import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuGroup,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuSub,
    DropdownMenuSubContent,
    DropdownMenuSubTrigger,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
    Command,
    CommandEmpty,
    CommandGroup,
    CommandInput,
    CommandItem,
    CommandList,
} from "@/components/ui/command"
import { Button } from "@/components/ui/button"
import { Check, ListFilter } from "lucide-react"
import { cn } from "@/lib/utils"
import { PaperItem } from "@/lib/schema";
import { PaperStatusEnum } from "@/components/utils/PdfStatus";

export type Filter = {
    type: "author" | "keyword" | "status" | "tag"
    value: string
}

export const NO_TAGS_FILTER_VALUE = "__NO_TAGS__";

export type Sort = {
    type: "publish_date"
    order: "asc" | "desc"
}

interface PaperFilteringProps {
    papers: PaperItem[]
    onFilterChange: (filters: Filter[]) => void
    onSortChange: (sort: Sort) => void
    filters: Filter[]
    sort: Sort
    showSort?: boolean
    className?: string
}

export function PaperFiltering({ papers, onFilterChange, onSortChange, filters, sort, showSort = true, className }: PaperFilteringProps) {
    const authors = Array.from(new Set(papers.flatMap(p => p.authors || [])))
    const keywords = Array.from(new Set(papers.flatMap(p => p.keywords || [])))
    const tags = Array.from(new Set(papers.flatMap(p => p.tags?.map(t => t.name) || [])))

    const handleSelectFilter = (filter: Filter) => {
        const newFilters = filters.some(f => f.type === filter.type && f.value === filter.value)
            ? filters.filter(f => !(f.type === filter.type && f.value === filter.value))
            : [...filters, filter]
        onFilterChange(newFilters)
    }

    const handleSelectSort = (newSort: Sort) => {
        onSortChange(newSort)
    }

    const isFilterActive = (filter: Filter) => {
        return filters.some(f => f.type === filter.type && f.value === filter.value)
    }

    const isSortActive = (newSort: Sort) => {
        return sort.type === newSort.type && sort.order === newSort.order
    }

    return (
        <div className={cn("shrink-0", className)}>
            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                    <Button
                        variant="outline"
                        className={cn("h-10 gap-2 px-3", filters.length > 0 && "border-brand/40 text-brand")}
                    >
                        <ListFilter className="size-4" />
                        <span className="max-[359px]:sr-only">{showSort ? "Filter & Sort" : "Filter"}</span>
                        {filters.length > 0 && (
                            <span className="-mr-1 min-w-5 rounded-full bg-brand px-1.5 text-center text-[11px] leading-5 font-semibold text-brand-foreground tabular-nums">
                                {filters.length}
                            </span>
                        )}
                    </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent className="w-56" align="end">
                    {showSort &&
                        <>
                            <DropdownMenuLabel>Sort by</DropdownMenuLabel>
                            <DropdownMenuGroup>
                                <DropdownMenuItem onSelect={() => handleSelectSort({ type: "publish_date", order: "desc" })}>
                                    Publish Date (Newest)
                                    {isSortActive({ type: "publish_date", order: "desc" }) && <Check className="ml-auto h-4 w-4" />}
                                </DropdownMenuItem>
                                <DropdownMenuItem onSelect={() => handleSelectSort({ type: "publish_date", order: "asc" })}>
                                    Publish Date (Oldest)
                                    {isSortActive({ type: "publish_date", order: "asc" }) && <Check className="ml-auto h-4 w-4" />}
                                </DropdownMenuItem>

                            </DropdownMenuGroup>
                            <DropdownMenuSeparator />
                        </>
                    }
                    <DropdownMenuLabel>Filter by</DropdownMenuLabel>
                    <DropdownMenuGroup>
                        <DropdownMenuSub>
                            <DropdownMenuSubTrigger>Authors</DropdownMenuSubTrigger>
                            <DropdownMenuSubContent className="p-0">
                                <Command>
                                    <CommandInput
                                        placeholder="Filter author..."
                                        autoFocus={true}
                                        className="h-9"
                                    />
                                    <CommandList>
                                        <CommandEmpty>No author found.</CommandEmpty>
                                        <CommandGroup>
                                            {authors.map(author => (
                                                <CommandItem
                                                    key={author}
                                                    value={author}
                                                    onSelect={() => handleSelectFilter({ type: "author", value: author })}
                                                >
                                                    {isFilterActive({ type: "author", value: author }) && <Check className="mr-2 h-4 w-4" />}
                                                    {author}
                                                </CommandItem>
                                            ))}
                                        </CommandGroup>
                                    </CommandList>
                                </Command>
                            </DropdownMenuSubContent>
                        </DropdownMenuSub>
                        <DropdownMenuSub>
                            <DropdownMenuSubTrigger>Keywords</DropdownMenuSubTrigger>
                            <DropdownMenuSubContent className="p-0">
                                <Command>
                                    <CommandInput
                                        placeholder="Filter keyword..."
                                        autoFocus={true}
                                        className="h-9"
                                    />
                                    <CommandList>
                                        <CommandEmpty>No keyword found.</CommandEmpty>
                                        <CommandGroup>
                                            {keywords.map(keyword => (
                                                <CommandItem
                                                    key={keyword}
                                                    value={keyword}
                                                    onSelect={() => handleSelectFilter({ type: "keyword", value: keyword })}
                                                >
                                                    {isFilterActive({ type: "keyword", value: keyword }) && <Check className="mr-2 h-4 w-4" />}
                                                    {keyword}
                                                </CommandItem>
                                            ))}
                                        </CommandGroup>
                                    </CommandList>
                                </Command>
                            </DropdownMenuSubContent>
                        </DropdownMenuSub>
                        <DropdownMenuSub>
                            <DropdownMenuSubTrigger>Tags</DropdownMenuSubTrigger>
                            <DropdownMenuSubContent className="p-0">
                                <Command>
                                    <CommandInput
                                        placeholder="Filter tag..."
                                        autoFocus={true}
                                        className="h-9"
                                    />
                                    <CommandList>
                                        <CommandEmpty>No tags found.</CommandEmpty>
                                        <CommandGroup>
                                            <CommandItem
                                                value="No tags"
                                                onSelect={() => handleSelectFilter({ type: "tag", value: NO_TAGS_FILTER_VALUE })}
                                                className="text-muted-foreground"
                                            >
                                                {isFilterActive({ type: "tag", value: NO_TAGS_FILTER_VALUE }) && <Check className="mr-2 h-4 w-4" />}
                                                No tags
                                            </CommandItem>
                                            {tags.map(tag => (
                                                <CommandItem
                                                    key={tag}
                                                    value={tag}
                                                    onSelect={() => handleSelectFilter({ type: "tag", value: tag })}
                                                >
                                                    {isFilterActive({ type: "tag", value: tag }) && <Check className="mr-2 h-4 w-4" />}
                                                    {tag}
                                                </CommandItem>
                                            ))}
                                        </CommandGroup>
                                    </CommandList>
                                </Command>
                            </DropdownMenuSubContent>
                        </DropdownMenuSub>
                        <DropdownMenuSub>
                            <DropdownMenuSubTrigger>Status</DropdownMenuSubTrigger>
                            <DropdownMenuSubContent className="p-0">
                                <Command>
                                    <CommandList>
                                        <CommandGroup>
                                            {Object.values(PaperStatusEnum).map(status => (
                                                <CommandItem
                                                    key={status}
                                                    value={status}
                                                    onSelect={() => handleSelectFilter({ type: "status", value: status })}
                                                >
                                                    {isFilterActive({ type: "status", value: status }) && <Check className="mr-2 h-4 w-4" />}
                                                    {status}
                                                </CommandItem>
                                            ))}
                                        </CommandGroup>
                                    </CommandList>
                                </Command>
                            </DropdownMenuSubContent>
                        </DropdownMenuSub>
                    </DropdownMenuGroup>
                </DropdownMenuContent>
            </DropdownMenu>

        </div>
    )
}
