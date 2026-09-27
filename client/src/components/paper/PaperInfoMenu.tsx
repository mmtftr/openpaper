"use client";

import { useState } from "react";
import { Archive, ArchiveRestore, ExternalLink, Info } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Drawer, DrawerContent, DrawerTitle, DrawerTrigger } from "@/components/ui/drawer";
import { CitationView } from "@/components/CitePaperButton";
import { PaperProjects } from "@/components/PaperProjects";
import { CodeViewerProvider } from "@/components/code/CodeViewerProvider";
import { RepoConnectPanel } from "@/components/code/RepoConnectPanel";
import { IngestStatusDialog, IngestStatusInfoButton } from "@/components/ingest/IngestStatus";
import { getStatusIcon, PaperStatusEnum, type PaperStatus } from "@/components/utils/PdfStatus";
import { useIsMobile } from "@/hooks/use-mobile";
import { useFeatureGate } from "@/hooks/useIngest";
import { archivePapersWithToast } from "@/hooks/usePapers";
import type { PaperData } from "@/lib/schema";
import { cn, isDateValid } from "@/lib/utils";
import { paperAtom, parentPaperIdAtom } from "./paperStore";
import { usePaperAtomValue, usePaperStore } from "./PaperStoreProvider";
import { useUpdatePaperStatus } from "./useUpdatePaperStatus";

const STATUSES: { value: PaperStatus; label: string }[] = [
    { value: PaperStatusEnum.TODO, label: "Todo" },
    { value: PaperStatusEnum.READING, label: "Reading" },
    { value: PaperStatusEnum.COMPLETED, label: "Completed" },
];

const STATUS_DOT: Record<PaperStatus, string> = {
    todo: "bg-yellow-500",
    reading: "bg-blue-500",
    completed: "bg-green-500",
};

const AUTHORS_SHOWN = 6;
/** More institutions than this are clamped to two lines until expanded. */
const INSTITUTIONS_SHOWN = 3;

const googleScholarUrl = (q: string) => `https://scholar.google.com/scholar?q=${encodeURIComponent(q)}`;

/**
 * The paper's one control in the app header: an info button opening a panel
 * (a bottom drawer on phones) with the metadata, reading status, projects,
 * citation, companion code repo, processing state and archiving.
 *
 * Owns a code-viewer provider so "Browse code" works from the header, and
 * the processing dialog, which opens after the panel closes.
 */
export function PaperInfoMenu() {
    const paper = usePaperAtomValue(paperAtom);
    const paperId = usePaperAtomValue(parentPaperIdAtom);
    const isMobile = useIsMobile();
    const [open, setOpen] = useState(false);
    const [processingOpen, setProcessingOpen] = useState(false);

    if (!paper || !paperId) return null;

    const status = paper.status as PaperStatus | undefined;
    const trigger = (
        <Button
            variant="ghost"
            size="icon"
            className="relative size-8 text-muted-foreground hover:text-foreground data-[state=open]:bg-accent data-[state=open]:text-foreground"
            title="Paper info"
            aria-label={status ? `Paper info (${status})` : "Paper info"}
        >
            <Info className="size-4" aria-hidden="true" />
            {status && STATUS_DOT[status] && (
                <span
                    aria-hidden="true"
                    className={cn(
                        "absolute right-1 bottom-1 size-1.5 rounded-full ring-2 ring-background transition-colors duration-200",
                        STATUS_DOT[status]
                    )}
                />
            )}
        </Button>
    );

    const body = (
        <PaperInfoBody
            paper={paper}
            paperId={paperId}
            onClose={() => setOpen(false)}
            onOpenProcessing={() => {
                setOpen(false);
                setProcessingOpen(true);
            }}
        />
    );

    return (
        <CodeViewerProvider paperId={paperId}>
            {isMobile ? (
                <Drawer open={open} onOpenChange={setOpen}>
                    <DrawerTrigger asChild>{trigger}</DrawerTrigger>
                    <DrawerContent>
                        <DrawerTitle className="sr-only">Paper info</DrawerTitle>
                        <div className="min-h-0 overflow-y-auto overscroll-contain pb-2">{body}</div>
                    </DrawerContent>
                </Drawer>
            ) : (
                <Popover open={open} onOpenChange={setOpen}>
                    <PopoverTrigger asChild>{trigger}</PopoverTrigger>
                    <PopoverContent
                        align="end"
                        collisionPadding={12}
                        className="w-[22rem] max-h-(--radix-popover-content-available-height) overflow-y-auto overscroll-contain p-0"
                    >
                        {body}
                    </PopoverContent>
                </Popover>
            )}
            <IngestStatusDialog open={processingOpen} onOpenChange={setProcessingOpen} />
        </CodeViewerProvider>
    );
}

function PaperInfoBody({
    paper,
    paperId,
    onClose,
    onOpenProcessing,
}: {
    paper: PaperData;
    paperId: string;
    onClose: () => void;
    onOpenProcessing: () => void;
}) {
    const updateStatus = useUpdatePaperStatus();
    const metadataGate = useFeatureGate(paperId, "metadata");
    const citeBlocked = metadataGate.ready && !metadataGate.enabled ? metadataGate.message : null;
    const [expanded, setExpanded] = useState(false);

    const authors = paper.authors ?? [];
    const institutions = paper.institutions ?? [];
    const shownAuthors = expanded ? authors : authors.slice(0, AUTHORS_SHOWN);
    const canExpand = authors.length > AUTHORS_SHOWN || institutions.length > INSTITUTIONS_SHOWN;
    const date = paper.publish_date && isDateValid(paper.publish_date) ? new Date(paper.publish_date) : null;
    const venue = paper.journal || paper.publisher;
    const meta = [date?.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }), venue].filter(Boolean);

    return (
        <div className="divide-y divide-border/60">
            <div className="space-y-2 px-4 pt-3.5 pb-3">
                <h3 className="text-sm font-semibold leading-snug text-pretty">{paper.title || "Untitled paper"}</h3>
                {(meta.length > 0 || paper.doi) && (
                    <p className="flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
                        {meta.map((m, i) => (
                            <span key={i} className="after:ml-1.5 after:content-['·'] last:after:content-none">
                                {m}
                            </span>
                        ))}
                        {paper.doi && (
                            <a
                                href={`https://doi.org/${paper.doi}`}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-0.5 hover:text-foreground hover:underline"
                            >
                                DOI
                                <ExternalLink className="size-3" />
                            </a>
                        )}
                    </p>
                )}
                {authors.length > 0 && (
                    <p className="text-xs leading-relaxed">
                        {shownAuthors.map((a, i) => (
                            <span key={i}>
                                <a
                                    href={googleScholarUrl(a)}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="text-brand hover:underline"
                                >
                                    {a}
                                </a>
                                {i < shownAuthors.length - 1 && <span className="text-muted-foreground">, </span>}
                            </span>
                        ))}
                        {!expanded && authors.length > AUTHORS_SHOWN && (
                            <span className="text-muted-foreground"> +{authors.length - AUTHORS_SHOWN}</span>
                        )}
                    </p>
                )}
                {institutions.length > 0 && (
                    <p className={cn("text-xs leading-relaxed text-muted-foreground", !expanded && "line-clamp-2")}>
                        {institutions.map((inst, i) => (
                            <span key={i}>
                                <a
                                    href={googleScholarUrl(inst)}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="hover:text-foreground hover:underline"
                                >
                                    {inst}
                                </a>
                                {i < institutions.length - 1 && "; "}
                            </span>
                        ))}
                    </p>
                )}
                {canExpand && (
                    <button
                        type="button"
                        onClick={() => setExpanded((v) => !v)}
                        aria-expanded={expanded}
                        className="text-xs text-muted-foreground hover:text-foreground hover:underline"
                    >
                        {expanded ? "Show less" : "Show all authors & affiliations"}
                    </button>
                )}
            </div>

            <Section title="Status">
                <div role="radiogroup" aria-label="Reading status" className="grid grid-cols-3 gap-1 rounded-lg bg-muted p-1">
                    {STATUSES.map(({ value, label }) => {
                        const active = paper.status === value;
                        return (
                            <button
                                key={value}
                                type="button"
                                role="radio"
                                aria-checked={active}
                                onClick={() => !active && updateStatus(value)}
                                className={cn(
                                    "flex items-center justify-center gap-1.5 rounded-md px-2 py-1 text-xs transition-[background-color,color,box-shadow] duration-200 ease-out-soft focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-brand/30 max-md:py-2 [&_svg]:size-3.5",
                                    active
                                        ? "bg-background font-medium text-foreground shadow-sm ring-1 ring-border/60 dark:ring-border"
                                        : "text-muted-foreground hover:text-foreground"
                                )}
                            >
                                {getStatusIcon(value)}
                                {label}
                            </button>
                        );
                    })}
                </div>
            </Section>

            <Section title="Projects">
                <PaperProjects id={paperId} />
            </Section>

            <Section title="Cite">
                {citeBlocked ? (
                    <p className="text-xs text-muted-foreground">{citeBlocked}</p>
                ) : (
                    <CitationView papers={[paper]} paperId={paperId} />
                )}
            </Section>

            <Section title="Code">
                <RepoConnectPanel paperId={paperId} onBrowse={onClose} />
            </Section>

            <ProcessingSection onOpen={onOpenProcessing} />

            <ArchiveSection archived={!!paper.archived_at} paperId={paperId} />
        </div>
    );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
    return (
        <section className="space-y-2 px-4 py-3">
            <h4 className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">{title}</h4>
            {children}
        </section>
    );
}

/** Processing row; renders nothing (not even the divider) for legacy papers. */
function ProcessingSection({ onOpen }: { onOpen: () => void }) {
    return (
        <div className="px-4 py-3 empty:hidden">
            <IngestStatusInfoButton onOpen={onOpen} />
        </div>
    );
}

/** Archive / unarchive the paper; it stays open here either way. */
function ArchiveSection({ archived, paperId }: { archived: boolean; paperId: string }) {
    const store = usePaperStore();
    const [pending, setPending] = useState(false);

    const toggle = async () => {
        setPending(true);
        await archivePapersWithToast([paperId], !archived, (now) =>
            store.set(paperAtom, (current) =>
                current ? { ...current, archived_at: now ? new Date().toISOString() : null } : current
            )
        );
        setPending(false);
    };

    return (
        <div className="flex items-center gap-2 px-4 py-2">
            {archived && (
                <p className="flex min-w-0 flex-1 items-center gap-1.5 text-xs text-muted-foreground">
                    <Archive className="size-3.5 shrink-0" aria-hidden="true" />
                    Archived: hidden from your library
                </p>
            )}
            <Button
                variant="ghost"
                size="sm"
                disabled={pending}
                onClick={toggle}
                className={cn(
                    "h-8 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground max-md:h-10",
                    archived ? "-mr-2 shrink-0" : "-ml-2"
                )}
            >
                {archived ? <ArchiveRestore className="size-3.5" /> : <Archive className="size-3.5" />}
                {archived ? "Unarchive" : "Archive paper"}
            </Button>
        </div>
    );
}
