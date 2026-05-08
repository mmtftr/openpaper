'use client';

import { fetchFromApi } from '@/lib/api';
import { useIsMobile } from '@/hooks/use-mobile';
import { subscribePaperChatStreaming } from '@/lib/paperDocEvents';
import dynamic from 'next/dynamic';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, FileText, Plus, Pencil, Trash2, Check, X, ChevronDown } from 'lucide-react';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
    cacheDocument,
    cacheDocumentList,
    clearQueuedDocPut,
    getCachedDocument,
    getCachedDocumentList,
    getQueuedDocPut,
    queueDocPut,
    useOnlineStatus,
} from '@/lib/offline';

const MilkdownImpl = dynamic(() => import('./PaperDocEditorImpl'), {
    ssr: false,
    loading: () => (
        <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">
            <Loader2 className="h-4 w-4 animate-spin mr-2" /> Loading editor…
        </div>
    ),
});

interface DocumentResponse {
    id: string;
    paper_id: string | null;
    title: string;
    content: string;
    revision: number;
    kind: string;
    updated_at?: string | null;
    offlineConflict?: unknown;
}

interface DocumentSummary {
    id: string;
    paper_id: string | null;
    title: string;
    revision: number;
    kind: string;
    updated_at?: string | null;
}

const AUTOSAVE_DEBOUNCE_MS = 800;
const MAX_DOC_BYTES = 1_000_000;
// While a chat turn is in flight we poll for agent-driven `write_main_doc`
// landings. The agent only writes MAIN today, so we skip the poll when the
// user is viewing a NOTE doc (no point hitting the network).
const AGENT_POLL_INTERVAL_MS = 2_000;

type Status =
    | { kind: 'idle' }
    | { kind: 'loading' }
    | { kind: 'saving' }
    | { kind: 'saved'; at: number }
    | { kind: 'queued-offline' }
    | { kind: 'too-large' }
    | { kind: 'conflict' }
    | { kind: 'error'; message: string };

function StatusRow({ status }: { status: Status }) {
    const [, setTick] = useState(0);
    useEffect(() => {
        if (status.kind !== 'saved') return;
        const interval = setInterval(() => setTick((t) => t + 1), 30_000);
        return () => clearInterval(interval);
    }, [status]);

    let text = '';
    let tone = 'text-muted-foreground';
    switch (status.kind) {
        case 'idle':
            text = '';
            break;
        case 'loading':
            text = 'Loading…';
            break;
        case 'saving':
            text = 'Saving…';
            break;
        case 'saved': {
            const seconds = Math.max(1, Math.round((Date.now() - status.at) / 1000));
            if (seconds < 60) text = `Saved · ${seconds}s ago`;
            else text = `Saved · ${Math.round(seconds / 60)}m ago`;
            break;
        }
        case 'queued-offline':
            text = 'Saved offline - will sync when reconnected.';
            tone = 'text-amber-600 dark:text-amber-400';
            break;
        case 'too-large':
            text = 'Document too large — trim to under 1MB to resume saving.';
            tone = 'text-amber-600 dark:text-amber-400';
            break;
        case 'conflict':
            text = 'Conflict — another tab or the agent edited this. Reload to merge.';
            tone = 'text-amber-600 dark:text-amber-400';
            break;
        case 'error':
            text = `Save failed: ${status.message}`;
            tone = 'text-red-600 dark:text-red-400';
            break;
    }
    if (!text) return null;
    return <div className={`text-xs px-2 py-1 ${tone}`}>{text}</div>;
}

interface PaperDocEditorProps {
    paperId: string;
}

export function PaperDocEditor({ paperId }: PaperDocEditorProps) {
    const isMobile = useIsMobile();
    const online = useOnlineStatus();
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

    // Initial fetch: list docs, then load MAIN (auto-created server-side if
    // missing). Subsequent switches are handled by the activeDocId effect.
    useEffect(() => {
        let cancelled = false;
        setStatus({ kind: 'loading' });
        (async () => {
            try {
                const cachedList = await getCachedDocumentList(paperId);
                if (!cancelled && cachedList) {
                    const docs = cachedList.documents as DocumentSummary[];
                    setDocs(docs);
                    const main = docs.find((d) => d.kind === 'main');
                    setActiveDocId(main?.id || docs[0]?.id || null);
                    setStatus({ kind: 'idle' });
                }

                const list: DocumentSummary[] = await fetchFromApi(
                    `/api/document?paper_id=${encodeURIComponent(paperId)}`
                );
                if (cancelled) return;
                setDocs(list);
                await cacheDocumentList(paperId, list);
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
                const cached = await getCachedDocument(activeDocId);
                if (!cancelled && cached) {
                    const cachedDoc = cached.data as DocumentResponse;
                    setDoc(cachedDoc);
                    setOverwriteContent(cachedDoc.content);
                    setOverwriteToken((t) => t + 1);
                    pendingContentRef.current = null;
                    setStatus(cachedDoc.offlineConflict ? { kind: 'conflict' } : { kind: 'idle' });
                }

                const response: DocumentResponse = await fetchFromApi(
                    `/api/document/${encodeURIComponent(activeDocId)}`
                );
                if (cancelled) return;
                const queued = await getQueuedDocPut(response.id);
                const cachedAfterFetch = await getCachedDocument(response.id);
                const hasOfflineConflict = Boolean(
                    cachedAfterFetch?.data &&
                    typeof cachedAfterFetch.data === 'object' &&
                    'offlineConflict' in cachedAfterFetch.data
                );
                const hydrated = queued
                    ? { ...response, content: queued.payload.content }
                    : response;
                setDoc(hydrated);
                await cacheDocument(response.id, hydrated);
                setOverwriteContent(hydrated.content);
                setOverwriteToken((t) => t + 1);
                pendingContentRef.current = queued ? queued.payload.content : null;
                setStatus(
                    hasOfflineConflict
                        ? { kind: 'conflict' }
                        : queued
                            ? { kind: 'queued-offline' }
                            : { kind: 'idle' }
                );
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
    }, [activeDocId]);

    const persist = useCallback(async () => {
        const current = docRef.current;
        const pending = pendingContentRef.current;
        if (!current || pending == null) return;
        if (inFlightRef.current) return;

        const byteLen = new TextEncoder().encode(pending).length;
        if (byteLen > MAX_DOC_BYTES) {
            setStatus({ kind: 'too-large' });
            return;
        }

        inFlightRef.current = true;
        setStatus({ kind: 'saving' });
        const sentContent = pending;
        const sentRevision = current.revision;
        const sentDocId = current.id;
        try {
            const apiBase = process.env.NEXT_PUBLIC_API_URL || '';
            const response = await fetch(`${apiBase}/api/document/${sentDocId}`, {
                method: 'PUT',
                credentials: 'include',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    content: sentContent,
                    expected_revision: sentRevision,
                }),
            });

            if (response.status === 413) {
                setStatus({ kind: 'too-large' });
                return;
            }
            if (response.status === 409) {
                const body = await response.json().catch(() => null);
                if (body && typeof body.current_revision === 'number' &&
                    typeof body.current_content === 'string') {
                    // Update our notion of the latest server state. Don't
                    // overwrite the editor — surface a Reload prompt instead.
                    if (docRef.current?.id === sentDocId) {
                        setDoc({
                            ...current,
                            revision: body.current_revision,
                            content: body.current_content,
                        });
                    }
                }
                setStatus({ kind: 'conflict' });
                return;
            }
            if (!response.ok) {
                const text = await response.text().catch(() => '');
                setStatus({
                    kind: 'error',
                    message: text || `HTTP ${response.status}`,
                });
                return;
            }

            const updated: DocumentResponse = await response.json();
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
                await clearQueuedDocPut(sentDocId);
            }
            setStatus({ kind: 'saved', at: Date.now() });
        } catch (e) {
            await queueDocPut({
                documentId: sentDocId,
                paperId: current.paper_id,
                content: sentContent,
                expectedRevision: sentRevision,
            });
            setStatus({ kind: 'queued-offline' });
        } finally {
            inFlightRef.current = false;
            if (pendingContentRef.current != null) {
                if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
                debounceTimerRef.current = setTimeout(persist, AUTOSAVE_DEBOUNCE_MS);
            }
        }
    }, []);

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
            void cacheDocument(current.id, { ...current, content: markdown });
            void queueDocPut({
                documentId: current.id,
                paperId: current.paper_id,
                content: markdown,
                expectedRevision: current.revision,
            });
            if (!online) {
                setStatus({ kind: 'queued-offline' });
                return;
            }
            if (status.kind === 'too-large' || status.kind === 'conflict') {
                // Block autosaves while the user resolves the situation.
                return;
            }
            if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
            debounceTimerRef.current = setTimeout(persist, AUTOSAVE_DEBOUNCE_MS);
        },
        [online, persist, status.kind]
    );

    // Flush any pending content synchronously and clear the debounce. Returns
    // when the in-flight write resolves so callers can sequence a doc switch
    // safely after.
    const flushPending = useCallback(async () => {
        if (debounceTimerRef.current) {
            clearTimeout(debounceTimerRef.current);
            debounceTimerRef.current = null;
        }
        if (pendingContentRef.current != null) {
            await persist();
        }
    }, [persist]);

    const handleReload = useCallback(async () => {
        if (!activeDocId) return;
        try {
            setStatus({ kind: 'loading' });
            const response: DocumentResponse = await fetchFromApi(
                `/api/document/${encodeURIComponent(activeDocId)}`
            );
            setDoc(response);
            setOverwriteContent(response.content);
            setOverwriteToken((t) => t + 1);
            pendingContentRef.current = null;
            setStatus({ kind: 'idle' });
        } catch (e) {
            setStatus({
                kind: 'error',
                message: e instanceof Error ? e.message : 'Reload failed',
            });
        }
    }, [activeDocId]);

    const handleKeepLocal = useCallback(async () => {
        const current = docRef.current;
        const localContent = pendingContentRef.current ?? current?.content;
        if (!current || localContent == null) return;
        pendingContentRef.current = localContent;
        await cacheDocument(current.id, { ...current, content: localContent, offlineConflict: undefined });
        await queueDocPut({
            documentId: current.id,
            paperId: current.paper_id,
            content: localContent,
            expectedRevision: current.revision,
        });
        setStatus({ kind: 'queued-offline' });
        if (online) {
            if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
            debounceTimerRef.current = setTimeout(persist, AUTOSAVE_DEBOUNCE_MS);
        }
    }, [online, persist]);

    const handleCopyLocal = useCallback(async () => {
        const localContent = pendingContentRef.current ?? docRef.current?.content ?? '';
        await navigator.clipboard.writeText(localContent);
    }, []);

    const handleSwitch = useCallback(
        async (id: string) => {
            setSwitcherOpen(false);
            if (id === activeDocId) return;
            await flushPending();
            setActiveDocId(id);
        },
        [activeDocId, flushPending]
    );

    const handleCreate = useCallback(async () => {
        if (!online) return;
        try {
            setSwitcherOpen(false);
            await flushPending();
            const created: DocumentResponse = await fetchFromApi('/api/document', {
                method: 'POST',
                body: JSON.stringify({ paper_id: paperId, title: 'New doc' }),
            });
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
    }, [online, paperId, flushPending]);

    const handleRenameSubmit = useCallback(
        async (id: string) => {
            if (!online) return;
            const title = renameDraft.trim();
            if (!title) {
                setRenamingId(null);
                return;
            }
            try {
                const updated: DocumentResponse = await fetchFromApi(
                    `/api/document/${encodeURIComponent(id)}`,
                    {
                        method: 'PATCH',
                        body: JSON.stringify({ title }),
                    }
                );
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
        [online, renameDraft]
    );

    const handleDelete = useCallback(
        async (id: string) => {
            if (!online) return;
            // window.confirm is fine for v1 — slice 3a doesn't introduce a
            // confirm-dialog primitive just for this one action.
            if (typeof window !== 'undefined' && !window.confirm('Delete this doc?')) {
                return;
            }
            try {
                await fetchFromApi(`/api/document/${encodeURIComponent(id)}`, {
                    method: 'DELETE',
                });
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
        [activeDocId, docs, online]
    );

    // While the agentic chat is streaming, poll for `write_main_doc` results.
    // Only meaningful when the user is viewing MAIN — the agent doesn't write
    // NOTE docs today, so polling them would be pointless network noise.
    const agentStreamingRef = useRef(false);
    useEffect(() => {
        const unsub = subscribePaperChatStreaming(paperId, (streaming) => {
            agentStreamingRef.current = streaming;
        });
        return unsub;
    }, [paperId]);

    useEffect(() => {
        let timer: ReturnType<typeof setInterval> | null = null;
        let cancelled = false;
        const tick = async () => {
            if (!agentStreamingRef.current) return;
            const current = docRef.current;
            if (!current) return;
            if (current.kind !== 'main') return;
            // Skip while the user is actively saving — refetching mid-PUT
            // would race with the response body update.
            if (inFlightRef.current) return;
            try {
                const response: DocumentResponse = await fetchFromApi(
                    `/api/document/main?paper_id=${encodeURIComponent(paperId)}`
                );
                if (cancelled) return;
                if (docRef.current?.id !== current.id) return;
                if (response.revision <= current.revision) return;
                if (pendingContentRef.current != null) {
                    setDoc((prev) => prev ? {
                        ...prev,
                        revision: response.revision,
                        content: response.content,
                    } : prev);
                    return;
                }
                setDoc(response);
                setOverwriteContent(response.content);
                setOverwriteToken((t) => t + 1);
            } catch {
                // Polling errors are noisy — swallow and try again next tick.
            }
        };
        timer = setInterval(() => {
            if (agentStreamingRef.current) tick();
        }, AGENT_POLL_INTERVAL_MS);
        return () => {
            cancelled = true;
            if (timer) clearInterval(timer);
        };
    }, [paperId]);

    // Flush in-flight content on unmount so we don't lose the last keystroke.
    useEffect(() => {
        return () => {
            if (debounceTimerRef.current) {
                clearTimeout(debounceTimerRef.current);
            }
            const pending = pendingContentRef.current;
            const current = docRef.current;
            if (!pending || !current) return;
            const byteLen = new TextEncoder().encode(pending).length;
            if (byteLen > MAX_DOC_BYTES) return;
            const apiBase = process.env.NEXT_PUBLIC_API_URL || '';
            const url = `${apiBase}/api/document/${current.id}`;
            const body = JSON.stringify({
                content: pending,
                expected_revision: current.revision,
            });
            // sendBeacon won't carry our auth cookie credentials reliably across
            // browsers in cross-origin setups; use a fire-and-forget fetch with
            // keepalive to ride out the unmount.
            try {
                fetch(url, {
                    method: 'PUT',
                    credentials: 'include',
                    headers: { 'Content-Type': 'application/json' },
                    body,
                    keepalive: true,
                });
            } catch {
                // Best-effort flush; the user can re-edit on next mount.
            }
        };
    }, []);

    const activeDoc = doc;
    const sortedDocs = [
        ...docs.filter((d) => d.kind === 'main'),
        ...docs.filter((d) => d.kind !== 'main'),
    ];

    return (
        <div className="flex flex-col h-full">
            <div className="flex items-center justify-between border-b border-border px-2 py-1 gap-2">
                <Popover open={switcherOpen} onOpenChange={setSwitcherOpen}>
                    <PopoverTrigger asChild>
                        <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 px-2 max-w-[60%] justify-start gap-1 font-medium"
                            disabled={!activeDoc}
                        >
                            <FileText className="h-3.5 w-3.5 shrink-0" />
                            <span className="truncate text-sm">
                                {activeDoc?.title || 'Loading…'}
                            </span>
                            <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-50" />
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
                                                disabled={!online}
                                            />
                                            <button
                                                type="button"
                                                onClick={() => handleRenameSubmit(d.id)}
                                                className="p-1 rounded hover:bg-accent"
                                                aria-label="Save"
                                                disabled={!online}
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
                                        className={`group flex items-center gap-1 px-2 py-1.5 rounded-sm text-sm hover:bg-accent ${isActive ? 'bg-accent/60' : ''
                                            }`}
                                    >
                                        <button
                                            type="button"
                                            onClick={() => handleSwitch(d.id)}
                                            className="flex-1 text-left truncate flex items-center gap-1.5 cursor-pointer"
                                        >
                                            <FileText className="h-3.5 w-3.5 shrink-0 opacity-70" />
                                            <span className="truncate">{d.title}</span>
                                        </button>
                                        {!isMain && (
                                            <button
                                                type="button"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    if (!online) return;
                                                    setRenameDraft(d.title);
                                                    setRenamingId(d.id);
                                                }}
                                                className="p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-background"
                                                aria-label="Rename"
                                                disabled={!online}
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
                                                className="p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-background text-red-600 dark:text-red-400"
                                                aria-label="Delete"
                                                disabled={!online}
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
                                className="w-full flex items-center gap-2 px-2 py-1.5 rounded-sm text-sm hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
                                disabled={!online}
                            >
                                <Plus className="h-3.5 w-3.5" />
                                <span>New doc</span>
                            </button>
                        </div>
                    </PopoverContent>
                </Popover>

                <div className="flex items-center gap-2">
                    <StatusRow status={status} />
                    {status.kind === 'conflict' && (
                        <>
                            <button
                                type="button"
                                onClick={handleKeepLocal}
                                className="text-xs px-2 py-1 rounded bg-amber-100 dark:bg-amber-950 hover:bg-amber-200 dark:hover:bg-amber-900 text-amber-900 dark:text-amber-100"
                            >
                                Keep local
                            </button>
                            <button
                                type="button"
                                onClick={handleReload}
                                className="text-xs px-2 py-1 rounded bg-amber-100 dark:bg-amber-950 hover:bg-amber-200 dark:hover:bg-amber-900 text-amber-900 dark:text-amber-100"
                            >
                                Reload
                            </button>
                            <button
                                type="button"
                                onClick={handleCopyLocal}
                                className="text-xs px-2 py-1 rounded bg-amber-100 dark:bg-amber-950 hover:bg-amber-200 dark:hover:bg-amber-900 text-amber-900 dark:text-amber-100"
                            >
                                Copy
                            </button>
                        </>
                    )}
                </div>
            </div>
            <div className={`min-h-0 flex-1 overflow-y-auto ${isMobile ? 'pb-24' : ''}`}>
                {activeDoc ? (
                    <MilkdownImpl
                        initialContent={activeDoc.content}
                        onChange={handleChange}
                        overwriteToken={overwriteToken}
                        overwriteContent={overwriteContent}
                    />
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
