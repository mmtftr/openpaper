"use client";

import { useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import useSWR from "swr";
import { useTheme } from "next-themes";
import { Compass, FileText, FolderKanban, Home, Library, Moon, Search, Settings, Sun } from "lucide-react";
import {
    Command,
    CommandEmpty,
    CommandGroup,
    CommandInput,
    CommandItem,
    CommandList,
    CommandSeparator,
} from "@/components/ui/command";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { PROJECTS_LIST_KEY } from "@/hooks/useProjects";
import { api, unwrap } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

const PAGES = [
    { href: "/", label: "Home", icon: Home },
    { href: "/papers", label: "Library", icon: Library },
    { href: "/projects", label: "Projects", icon: FolderKanban },
    { href: "/discover", label: "Discover", icon: Compass },
    { href: "/settings", label: "Settings", icon: Settings },
];

// With an empty query the palette shows only this many recent papers, so the
// projects and pages below stay in view.
const RECENT_PAPERS = 6;

// cmdk's default scorer is a loose subsequence match ("manifold" hits most
// titles); require every typed word to appear instead.
function filter(value: string, search: string, keywords?: string[]) {
    const haystack = (keywords?.length ? keywords.join(" ") : value).toLowerCase();
    return search
        .toLowerCase()
        .split(/\s+/)
        .filter(Boolean)
        .every((word) => haystack.includes(word))
        ? 1
        : 0;
}

/**
 * ⌘K: jump to any paper, project or page. The header's search button opens
 * it too. On the home page ⌘K belongs to the page's own search box.
 * `compact` keeps the button icon-only below `xl` (the paper page's header).
 */
export function CommandMenu({ className, compact = false }: { className?: string; compact?: boolean }) {
    const router = useRouter();
    const pathname = usePathname();
    const { user } = useAuth();
    const { resolvedTheme, setTheme } = useTheme();
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState("");

    useEffect(() => {
        if (pathname === "/") return;
        const onKey = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
                e.preventDefault();
                setOpen((o) => !o);
            }
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [pathname]);

    // Fetched once the menu is first opened, then kept fresh by SWR.
    const [wanted, setWanted] = useState(false);
    useEffect(() => {
        if (!open) return;
        setWanted(true);
        setQuery("");
    }, [open]);
    const { data: activePapers } = useSWR(
        user && wanted ? ["/api/paper/active"] : null,
        () => unwrap(api.GET("/api/paper/active")),
    );
    const { data: projects } = useSWR(
        user && wanted ? PROJECTS_LIST_KEY : null,
        () => unwrap(api.GET("/api/projects")),
    );
    const papers = useMemo(
        () =>
            [...(activePapers?.papers ?? [])].sort(
                (a, b) => new Date(b.created_at || "").getTime() - new Date(a.created_at || "").getTime()
            ),
        [activePapers]
    );

    // Papers usually arrive after the static pages have rendered, and cmdk
    // would keep "Home" selected; start on the most recent paper instead.
    const [selected, setSelected] = useState("");
    const firstPaperId = papers[0]?.id;
    useEffect(() => {
        if (open && firstPaperId) setSelected(`paper-${firstPaperId}`);
    }, [open, firstPaperId]);

    if (!user) return null;

    const go = (href: string) => {
        setOpen(false);
        router.push(href);
    };
    const shownPapers = query.trim() ? papers : papers.slice(0, RECENT_PAPERS);

    return (
        <>
            <button
                type="button"
                onClick={() => setOpen(true)}
                aria-label="Search papers and projects"
                className={cn(
                    "flex h-8 shrink-0 items-center gap-2 rounded-lg text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
                    compact
                        ? "max-xl:w-8 max-xl:justify-center xl:w-56 xl:border xl:bg-muted/40 xl:px-2.5"
                        : "max-md:w-8 max-md:justify-center md:w-56 md:border md:bg-muted/40 md:px-2.5 lg:w-64",
                    className
                )}
            >
                <Search className="size-4 shrink-0" />
                <span className={cn("hidden flex-1 text-left", compact ? "xl:inline" : "md:inline")}>Search…</span>
                <kbd
                    className={cn(
                        "hidden rounded border bg-background px-1.5 font-sans text-[10px] font-medium",
                        compact ? "xl:inline" : "md:inline"
                    )}
                >
                    ⌘K
                </kbd>
            </button>

            <Dialog open={open} onOpenChange={setOpen}>
                <DialogContent showCloseButton={false} className="top-[15%] translate-y-0 overflow-hidden p-0 sm:max-w-xl">
                    <DialogTitle className="sr-only">Search</DialogTitle>
                    <DialogDescription className="sr-only">Jump to a paper, project or page</DialogDescription>
                    <Command
                        filter={filter}
                        value={selected}
                        onValueChange={setSelected}
                        className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-muted-foreground [&_[cmdk-group]]:px-2 [&_[cmdk-input-wrapper]]:h-12 [&_[cmdk-item]]:px-2 [&_[cmdk-item]]:py-2.5 [&_[cmdk-item]_svg]:size-4"
                    >
                        <CommandInput value={query} onValueChange={setQuery} placeholder="Search papers, projects, pages…" />
                        <CommandList className="max-h-[min(60dvh,420px)]">
                            <CommandEmpty>Nothing matches.</CommandEmpty>
                            {shownPapers.length > 0 && (
                                <CommandGroup heading={query.trim() ? "Papers" : "Recent papers"}>
                                    {shownPapers.map((paper) => (
                                        <CommandItem
                                            key={paper.id}
                                            value={`paper-${paper.id}`}
                                            keywords={[paper.title || "Untitled"]}
                                            onSelect={() => go(`/paper/${paper.id}`)}
                                        >
                                            <FileText />
                                            <span className="truncate">{paper.title || "Untitled"}</span>
                                        </CommandItem>
                                    ))}
                                </CommandGroup>
                            )}
                            {projects && projects.length > 0 && (
                                <CommandGroup heading="Projects">
                                    {projects.map((project) => (
                                        <CommandItem
                                            key={project.id}
                                            value={`project-${project.id}`}
                                            keywords={[project.title ?? ""]}
                                            onSelect={() => go(`/projects/${project.id}`)}
                                        >
                                            <FolderKanban />
                                            <span className="truncate">{project.title}</span>
                                        </CommandItem>
                                    ))}
                                </CommandGroup>
                            )}
                            <CommandSeparator />
                            <CommandGroup heading="Go to">
                                {PAGES.map(({ href, label, icon: Icon }) => (
                                    <CommandItem key={href} value={`page-${href}`} keywords={[label]} onSelect={() => go(href)}>
                                        <Icon />
                                        {label}
                                    </CommandItem>
                                ))}
                                <CommandItem
                                    value="theme"
                                    keywords={["theme dark light mode"]}
                                    onSelect={() => {
                                        setTheme(resolvedTheme === "dark" ? "light" : "dark");
                                        setOpen(false);
                                    }}
                                >
                                    {resolvedTheme === "dark" ? <Sun /> : <Moon />}
                                    {resolvedTheme === "dark" ? "Light mode" : "Dark mode"}
                                </CommandItem>
                            </CommandGroup>
                        </CommandList>
                    </Command>
                </DialogContent>
            </Dialog>
        </>
    );
}
