'use client';

import { api, ApiRequestError, unwrap, type Schemas } from '@/lib/api/client';
import { agentDocWritesAtom } from '@/lib/paperDocRevision';
import { useAtomValue } from 'jotai';
import dynamic from 'next/dynamic';
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { AlertTriangle, Loader2, FileText, Plus, Pencil, Trash2, Check, X, ChevronDown } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

const MilkdownImpl = dynamic(() => import('./PaperDocEditorImpl'), {
    ssr: false,
    loading: () => (
        <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">
            <Loader2 className="h-4 w-4 animate-spin mr-2" /> Loading editor…
        </div>
    ),
});

type DocumentResponse = Schemas["DocumentResponse"];
type DocumentSummary = Schemas["DocumentSummary"];

const getDocument = (documentId: string) =>
    unwrap(api.GET("/api/document/{document_id}", { params: { path: { document_id: documentId } } }));

const listDocuments = (paperId: string) =>
    unwrap(api.GET("/api/document", { params: { query: { paper_id: paperId } } }));

const AUTOSAVE_DEBOUNCE_MS = 800;
const MAX_DOC_BYTES = 1_000_000;

type Status =
    | { kind: 'idle' }
    | { kind: 'loading' }
    | { kind: 'saving' }
    | { kind: 'saved'; at: number }
    | { kind: 'too-large' }
    | { kind: 'conflict' }
    | { kind: 'error'; message: string };

function StatusRow({ status }: { status: Status }) {
    let text = '';
    let title: string | undefined;
    let icon: ReactNode = null;
    let tone = 'text-muted-foreground';
    switch (status.kind) {
        case 'idle':
        case 'loading':
            return null;
        case 'saving':
            text = 'Saving…';
            icon = <Loader2 className="h-3 w-3 animate-spin" />;
            break;
        case 'saved':
            text = 'Saved';
            title = `Saved at ${new Date(status.at).toLocaleTimeString()}`;
            icon = <Check className="h-3 w-3" />;
            break;
        case 'too-large':
            text = 'Too large to save';
            title = 'Document too large — trim to under 1MB to resume saving.';
            tone = 'text-amber-600 dark:text-amber-400';
            icon = <AlertTriangle className="h-3 w-3" />;
            break;
        case 'conflict':
            text = 'Edited elsewhere';
            title = 'Conflict — another tab or the agent edited this. Reload to merge.';
            tone = 'text-amber-600 dark:text-amber-400';
            icon = <AlertTriangle className="h-3 w-3" />;
            break;
        case 'error':
            text = status.message;
            title = status.message;
            tone = 'text-red-600 dark:text-red-400';
            icon = <AlertTriangle className="h-3 w-3" />;
            break;
    }
    return (
        <div
            className={`flex min-w-0 items-center gap-1 text-xs animate-in fade-in duration-200 ${tone}`}
            title={title}
            role="status"
        >
            <span className="shrink-0">{icon}</span>
            <span className="truncate">{text}</span>
        </div>
    );
}

interface PaperDocEditorProps {
    paperId: string;
}

export function PaperDocEditor({ paperId }: PaperDocEditorProps) {
    const [docs, setDocs] = useState<DocumentSummary[]>([]);
    const [activeDocId, setActiveDocId] = useState<string | null>(null);
    const [doc, setDoc] = useState<DocumentResponse | null>(null);
    const [status, setStatus] = useState<Status>({ kind: 'loading' });
    const [switcherOpen, setSwitcherOpen] = useState(false);
    const [renamingId, setRenamingId] = useState<string | null>(null);
    const [renameDraft, setRenameDraft] = useState('');

    // We bump overwriteToken whenever the parent decides the editor must be
    // rehydrated from a server snapshot (initial load, doc switch, 409 reload).
    const [overwriteToken, setOverwriteToken] = useState(0);
    const [overwriteContent, setOverwriteContent] = useState('');

    const pendingContentRef = useRef<string | null>(null);
    const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const inFlightRef = useRef(false);
    const docRef = useRef<DocumentResponse | null>(null);
    docRef.current = doc;
    // Set on a 409 against someone else's write; cleared only when the user
    // resolves it (Reload) or leaves this doc. While set, no save path
    // (autosave, doc-switch flush, unmount flush) may write the stale draft.
    const conflictRef = useRef(false);
    const [inConflict, setInConflict] = useState(false);
    const setConflict = useCallback((value: boolean) => {
        conflictRef.current = value;
        setInConflict(value);
    }, []);

    // Initial fetch: list docs, then load MAIN (auto-created server-side if
    // missing). Subsequent switches are handled by the activeDocId effect.
    useEffect(() => {
        let cancelled = false;
        setStatus({ kind: 'loading' });
        (async () => {
            try {
                const list = await listDocuments(paperId);
                if (cancelled) return;
                setDocs(list);
                const main = list.find((d) => d.kind === 'main');
                const initial = main?.id || list[0]?.id || null;
                setActiveDocId(initial);
                if (!initial) {
                    setStatus({ kind: 'error', message: 'No document available' });
                }
            } catch (e) {
                if (cancelled) return;
                setStatus({
                    kind: 'error',
                    message: e instanceof Error ? e.message : 'Could not load documents',
                });
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [paperId]);

    // Hydrate the editor whenever the active doc changes.
    useEffect(() => {
        if (!activeDocId) return;
        let cancelled = false;
        setStatus({ kind: 'loading' });
        (async () => {
            try {
                const response = await getDocument(activeDocId);
                if (cancelled) return;
                setDoc(response);
                setOverwriteContent(response.content);
                setOverwriteToken((t) => t + 1);
                pendingContentRef.current = null;
                // A different doc: the previous one's unsaved conflicting
                // draft was dropped (never saved), so there's nothing to resolve.
                setConflict(false);
                setStatus({ kind: 'idle' });
            } catch (e) {
                if (cancelled) return;
                setStatus({
                    kind: 'error',
                    message: e instanceof Error ? e.message : 'Could not load document',
                });
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [activeDocId, setConflict]);

    const persist = useCallback(async () => {
        const current = docRef.current;
        const pending = pendingContentRef.current;
        if (!current || pending == null) return;
        if (inFlightRef.current) return;
        if (conflictRef.current) return;

        const byteLen = new TextEncoder().encode(pending).length;
        if (byteLen > MAX_DOC_BYTES) {
            setStatus({ kind: 'too-large' });
            return;
        }

        inFlightRef.current = true;
        setStatus({ kind: 'saving' });
        const sentContent = pending;
        const sentDocId = current.id;
        // Set when the save ends in a conflict a blind retry can't fix; the
        // `finally` below then doesn't re-schedule another PUT.
        let stopRetrying = false;
        const put = (expectedRevision: number) =>
            unwrap(api.PUT("/api/document/{document_id}", {
                params: { path: { document_id: sentDocId } },
                body: { content: sentContent, expected_revision: expectedRevision },
            }));
        // A stale-revision 409 carries the server's current revision/content.
        const conflictOf = (e: unknown) =>
            e instanceof ApiRequestError && e.status === 409
                ? (e.body as Schemas["RevisionConflictError"])
                : null;
        // Stop saving and show the conflict prompt; the user's text stays in
        // the editor until they reload. The base revision is deliberately NOT
        // advanced to the server's: any save against it would 409 again
        // rather than overwrite the other writer's content.
        const enterConflict = () => {
            stopRetrying = true;
            if (docRef.current?.id !== sentDocId) return;
            setConflict(true);
            setStatus({ kind: 'conflict' });
        };
        try {
            let updated: DocumentResponse;

            try {
                updated = await put(current.revision);
            } catch (e) {
                const latest = conflictOf(e);
                if (!latest) throw e;
                // The revision moved since our last load/save.
                if (docRef.current?.id !== sentDocId) return;
                if (latest.current_content === sentContent) {
                    // The server already holds exactly our text.
                    updated = { ...current, revision: latest.current_revision, content: latest.current_content };
                } else if (latest.current_content !== current.content) {
                    // Someone else (the chat agent, another tab) wrote different
                    // content: don't overwrite it, let the user reload.
                    enterConflict();
                    return;
                } else {
                    // Only the revision moved; the content is still our last
                    // known base, so nothing would be lost. Retry on top of it.
                    try {
                        updated = await put(latest.current_revision);
                    } catch (retryError) {
                        const again = conflictOf(retryError);
                        if (!again) throw retryError;
                        // Lost the race again; stop and let the user reload.
                        enterConflict();
                        return;
                    }
                }
            }
            // Only adopt the response if the user didn't switch docs while we
            // were saving — otherwise overwriting `doc` would clobber the
            // freshly-loaded target doc with the old one's content.
            if (docRef.current?.id === sentDocId) {
                setDoc(updated);
            }
            // Keep doc list in sync for revision/timestamp display.
            setDocs((prev) =>
                prev.map((d) =>
                    d.id === updated.id
                        ? { ...d, revision: updated.revision, updated_at: updated.updated_at }
                        : d
                )
            );

            if (pendingContentRef.current === sentContent) {
                pendingContentRef.current = null;
            }
            setStatus({ kind: 'saved', at: Date.now() });
        } catch (e) {
            if (e instanceof ApiRequestError && e.status === 413) {
                setStatus({ kind: 'too-large' });
                return;
            }
            setStatus({
                kind: 'error',
                message: `Save failed: ${e instanceof Error ? e.message : 'network error'}`,
            });
        } finally {
            inFlightRef.current = false;
            if (pendingContentRef.current != null && !stopRetrying) {
                if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
                debounceTimerRef.current = setTimeout(persist, AUTOSAVE_DEBOUNCE_MS);
            }
        }
    }, [setConflict]);

    const handleChange = useCallback(
        (markdown: string) => {
            const current = docRef.current;
            if (!current) return;
            // Suppress the listener event the editor emits right after we
            // hydrate it with server content.
            if (markdown === current.content && pendingContentRef.current == null) {
                return;
            }
            pendingContentRef.current = markdown;
            if (status.kind === 'too-large' || conflictRef.current) {
                // Block autosaves while the user resolves the situation.
                return;
            }
            if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
            debounceTimerRef.current = setTimeout(persist, AUTOSAVE_DEBOUNCE_MS);
        },
        [persist, status.kind]
    );

    // Flush any pending content synchronously and clear the debounce. Returns
    // when the in-flight write resolves so callers can sequence a doc switch
    // safely after.
    const flushPending = useCallback(async () => {
        if (debounceTimerRef.current) {
            clearTimeout(debounceTimerRef.current);
            debounceTimerRef.current = null;
        }
        // In conflict, `persist` refuses to save: switching docs drops the
        // stale draft rather than writing it over the other writer's content.
        if (pendingContentRef.current != null) {
            await persist();
        }
    }, [persist]);

    // Leaving a doc mid-conflict drops the unsaved draft (it's never written
    // over the other change), so ask first.
    const confirmDropConflict = useCallback(
        () =>
            !conflictRef.current ||
            typeof window === 'undefined' ||
            window.confirm('This doc has unsaved edits that conflict with a newer version. Discard them?'),
        []
    );

    const handleReload = useCallback(async () => {
        if (!activeDocId) return;
        try {
            setStatus({ kind: 'loading' });
            const response = await getDocument(activeDocId);
            setDoc(response);
            setOverwriteContent(response.content);
            setOverwriteToken((t) => t + 1);
            pendingContentRef.current = null;
            setConflict(false);
            setStatus({ kind: 'idle' });
        } catch (e) {
            setStatus({
                kind: 'error',
                message: e instanceof Error ? e.message : 'Reload failed',
            });
        }
    }, [activeDocId, setConflict]);

    const handleSwitch = useCallback(
        async (id: string) => {
            setSwitcherOpen(false);
            if (id === activeDocId) return;
            if (!confirmDropConflict()) return;
            await flushPending();
            setActiveDocId(id);
        },
        [activeDocId, confirmDropConflict, flushPending]
    );

    const handleCreate = useCallback(async () => {
        try {
            setSwitcherOpen(false);
            if (!confirmDropConflict()) return;
            await flushPending();
            const created = await unwrap(api.POST('/api/document', {
                body: { paper_id: paperId, title: 'New doc' },
            }));
            const summary: DocumentSummary = {
                id: created.id,
                paper_id: created.paper_id,
                title: created.title,
                revision: created.revision,
                kind: created.kind,
                updated_at: created.updated_at,
            };
            setDocs((prev) => [...prev, summary]);
            setActiveDocId(created.id);
        } catch (e) {
            setStatus({
                kind: 'error',
                message: e instanceof Error ? e.message : 'Could not create doc',
            });
        }
    }, [paperId, confirmDropConflict, flushPending]);

    const handleRenameSubmit = useCallback(
        async (id: string) => {
            const title = renameDraft.trim();
            if (!title) {
                setRenamingId(null);
                return;
            }
            try {
                const updated = await unwrap(api.PATCH('/api/document/{document_id}', {
                    params: { path: { document_id: id } },
                    body: { title },
                }));
                setDocs((prev) =>
                    prev.map((d) =>
                        d.id === id
                            ? { ...d, title: updated.title, updated_at: updated.updated_at }
                            : d
                    )
                );
                if (docRef.current?.id === id) {
                    setDoc((prev) => (prev ? { ...prev, title: updated.title } : prev));
                }
            } catch (e) {
                setStatus({
                    kind: 'error',
                    message: e instanceof Error ? e.message : 'Rename failed',
                });
            } finally {
                setRenamingId(null);
            }
        },
        [renameDraft]
    );

    const handleDelete = useCallback(
        async (id: string) => {
            // window.confirm is fine for v1 — slice 3a doesn't introduce a
            // confirm-dialog primitive just for this one action.
            if (typeof window !== 'undefined' && !window.confirm('Delete this doc?')) {
                return;
            }
            try {
                await unwrap(api.DELETE('/api/document/{document_id}', {
                    params: { path: { document_id: id } },
                }));
                setDocs((prev) => prev.filter((d) => d.id !== id));
                if (activeDocId === id) {
                    const main = docs.find((d) => d.kind === 'main' && d.id !== id);
                    setActiveDocId(main?.id || null);
                }
            } catch (e) {
                setStatus({
                    kind: 'error',
                    message: e instanceof Error ? e.message : 'Delete failed',
                });
            }
        },
        [activeDocId, docs]
    );

    // The chat agent's `write_doc` results bump this paper's counter as they
    // stream in (see paperChatSessions.ts). On a bump, refetch the doc list
    // (the agent may have created a NOTE) and the open doc. A mount reads
    // fresh data anyway, so only changes after mount count.
    const agentDocWrites = useAtomValue(agentDocWritesAtom)[paperId] ?? 0;
    const seenAgentDocWritesRef = useRef(agentDocWrites);
    useEffect(() => {
        if (agentDocWrites === seenAgentDocWritesRef.current) return;
        seenAgentDocWritesRef.current = agentDocWrites;
        let cancelled = false;
        (async () => {
            try {
                const list = await listDocuments(paperId);
                if (!cancelled) setDocs(list);
            } catch {
                // Keep the current list; the next write or remount retries.
            }
            const current = docRef.current;
            if (cancelled || !current) return;
            // Unsaved or in-flight user edits: leave the editor and our base
            // revision alone, so their save hits the revision check and the
            // conflict prompt instead of silently replacing the agent's write.
            if (pendingContentRef.current != null || inFlightRef.current) return;
            try {
                const response = await getDocument(current.id);
                if (cancelled) return;
                if (docRef.current?.id !== current.id) return;
                if (response.revision <= current.revision) return;
                if (pendingContentRef.current != null || inFlightRef.current) return;
                setDoc(response);
                setOverwriteContent(response.content);
                setOverwriteToken((t) => t + 1);
            } catch {
                // The doc stays as it is; the next write or remount retries.
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [agentDocWrites, paperId]);

    // Flush in-flight content on unmount so we don't lose the last keystroke.
    useEffect(() => {
        return () => {
            if (debounceTimerRef.current) {
                clearTimeout(debounceTimerRef.current);
            }
            const pending = pendingContentRef.current;
            const current = docRef.current;
            if (!pending || !current) return;
            // Never write a conflicting draft over someone else's content.
            if (conflictRef.current) return;
            const byteLen = new TextEncoder().encode(pending).length;
            if (byteLen > MAX_DOC_BYTES) return;
            // sendBeacon won't carry our auth cookie credentials reliably across
            // browsers in cross-origin setups; use a fire-and-forget fetch with
            // keepalive to ride out the unmount.
            api.PUT('/api/document/{document_id}', {
                params: { path: { document_id: current.id } },
                body: { content: pending, expected_revision: current.revision },
                keepalive: true,
            }).catch(() => {
                // Best-effort flush; the user can re-edit on next mount.
            });
        };
    }, []);

    const activeDoc = doc;
    const sortedDocs = [
        ...docs.filter((d) => d.kind === 'main'),
        ...docs.filter((d) => d.kind !== 'main'),
    ];

    return (
        <div className="flex flex-col h-full">
            <div className="flex h-10 shrink-0 items-center justify-between gap-2 border-b border-border px-2">
                <Popover open={switcherOpen} onOpenChange={setSwitcherOpen}>
                    <PopoverTrigger asChild>
                        <Button
                            variant="ghost"
                            size="sm"
                            className="h-8 min-w-0 max-w-[60%] justify-start gap-1.5 px-2 font-medium"
                            disabled={!activeDoc}
                        >
                            <FileText className="h-3.5 w-3.5 shrink-0" />
                            <span className="truncate text-sm">
                                {activeDoc?.title || 'Loading…'}
                            </span>
                            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        </Button>
                    </PopoverTrigger>
                    <PopoverContent className="w-72 p-1" align="start">
                        <div className="max-h-72 overflow-y-auto">
                            {sortedDocs.map((d) => {
                                const isActive = d.id === activeDocId;
                                const isMain = d.kind === 'main';
                                if (renamingId === d.id) {
                                    return (
                                        <div
                                            key={d.id}
                                            className="flex items-center gap-1 px-1 py-1"
                                        >
                                            <Input
                                                autoFocus
                                                value={renameDraft}
                                                onChange={(e) => setRenameDraft(e.target.value)}
                                                onKeyDown={(e) => {
                                                    if (e.key === 'Enter') handleRenameSubmit(d.id);
                                                    else if (e.key === 'Escape') setRenamingId(null);
                                                }}
                                                className="h-7 text-sm"
                                                maxLength={200}
                                            />
                                            <button
                                                type="button"
                                                onClick={() => handleRenameSubmit(d.id)}
                                                className="p-1 rounded hover:bg-accent"
                                                aria-label="Save"
                                            >
                                                <Check className="h-3.5 w-3.5" />
                                            </button>
                                            <button
                                                type="button"
                                                onClick={() => setRenamingId(null)}
                                                className="p-1 rounded hover:bg-accent"
                                                aria-label="Cancel"
                                            >
                                                <X className="h-3.5 w-3.5" />
                                            </button>
                                        </div>
                                    );
                                }
                                return (
                                    <div
                                        key={d.id}
                                        className={`group flex items-center gap-1 rounded-sm px-2 py-1 text-sm hover:bg-accent ${isActive ? 'bg-accent/60' : ''}`}
                                    >
                                        <button
                                            type="button"
                                            onClick={() => handleSwitch(d.id)}
                                            className="flex min-h-9 flex-1 cursor-pointer items-center gap-1.5 truncate text-left md:min-h-7"
                                        >
                                            <FileText className="h-3.5 w-3.5 shrink-0 opacity-70" />
                                            <span className="truncate">{d.title}</span>
                                        </button>
                                        {!isMain && (
                                            <button
                                                type="button"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    setRenameDraft(d.title);
                                                    setRenamingId(d.id);
                                                }}
                                                className="rounded p-2 hover:bg-background md:p-1 pointer-fine:opacity-0 pointer-fine:group-hover:opacity-100 pointer-fine:group-focus-within:opacity-100"
                                                aria-label="Rename"
                                            >
                                                <Pencil className="h-3.5 w-3.5" />
                                            </button>
                                        )}
                                        {!isMain && (
                                            <button
                                                type="button"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    handleDelete(d.id);
                                                }}
                                                className="rounded p-2 text-red-600 hover:bg-background md:p-1 dark:text-red-400 pointer-fine:opacity-0 pointer-fine:group-hover:opacity-100 pointer-fine:group-focus-within:opacity-100"
                                                aria-label="Delete"
                                            >
                                                <Trash2 className="h-3.5 w-3.5" />
                                            </button>
                                        )}
                                    </div>
                                );
                            })}
                        </div>
                        <div className="border-t border-border mt-1 pt-1">
                            <button
                                type="button"
                                onClick={handleCreate}
                                className="flex min-h-9 w-full items-center gap-2 rounded-sm px-2 text-sm hover:bg-accent md:min-h-8"
                            >
                                <Plus className="h-3.5 w-3.5" />
                                <span>New doc</span>
                            </button>
                        </div>
                    </PopoverContent>
                </Popover>

                <div className="flex min-w-0 items-center gap-2">
                    <StatusRow status={status} />
                    {inConflict && (
                        <button
                            type="button"
                            onClick={handleReload}
                            className="shrink-0 text-xs px-2 py-1 rounded bg-amber-100 dark:bg-amber-950 hover:bg-amber-200 dark:hover:bg-amber-900 text-amber-900 dark:text-amber-100"
                        >
                            Reload
                        </button>
                    )}
                </div>
            </div>
            <div data-doc-scroll className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
                {activeDoc ? (
                    <MilkdownImpl
                        initialContent={activeDoc.content}
                        onChange={handleChange}
                        overwriteToken={overwriteToken}
                        overwriteContent={overwriteContent}
                    />
                ) : status.kind === 'error' ? (
                    <p className="px-4 py-8 text-center text-sm text-muted-foreground">{status.message}</p>
                ) : (
                    <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">
                        <Loader2 className="h-4 w-4 animate-spin mr-2" />
                        Loading…
                    </div>
                )}
            </div>
        </div>
    );
}
