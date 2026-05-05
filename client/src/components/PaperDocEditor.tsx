'use client';

import { fetchFromApi } from '@/lib/api';
import { useIsMobile } from '@/hooks/use-mobile';
import { subscribePaperChatStreaming } from '@/lib/paperDocEvents';
import dynamic from 'next/dynamic';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';

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
}

const AUTOSAVE_DEBOUNCE_MS = 800;
const MAX_DOC_BYTES = 1_000_000;
// While a chat turn is in flight we poll for agent-driven `write_main_doc`
// landings. 2s is wasteful but correct; SSE/websockets are slice-3 work.
const AGENT_POLL_INTERVAL_MS = 2_000;

type Status =
    | { kind: 'idle' }
    | { kind: 'loading' }
    | { kind: 'saving' }
    | { kind: 'saved'; at: number }
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
    const [doc, setDoc] = useState<DocumentResponse | null>(null);
    const [status, setStatus] = useState<Status>({ kind: 'loading' });

    // We bump overwriteToken whenever the parent decides the editor must be
    // rehydrated from a server snapshot (initial load, 409 reload).
    const [overwriteToken, setOverwriteToken] = useState(0);
    const [overwriteContent, setOverwriteContent] = useState('');

    const pendingContentRef = useRef<string | null>(null);
    const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const inFlightRef = useRef(false);
    const docRef = useRef<DocumentResponse | null>(null);
    docRef.current = doc;

    // Initial fetch.
    useEffect(() => {
        let cancelled = false;
        setStatus({ kind: 'loading' });
        (async () => {
            try {
                const response: DocumentResponse = await fetchFromApi(
                    `/api/document/main?paper_id=${encodeURIComponent(paperId)}`
                );
                if (cancelled) return;
                setDoc(response);
                setOverwriteContent(response.content);
                setOverwriteToken((t) => t + 1);
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
    }, [paperId]);

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
        try {
            const apiBase = process.env.NEXT_PUBLIC_API_URL || '';
            const response = await fetch(`${apiBase}/api/document/${current.id}`, {
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
                    // overwrite the editor in slice 1 — we surface a
                    // Reload prompt instead (less destructive than a silent
                    // overwrite of in-flight typing).
                    setDoc({
                        ...current,
                        revision: body.current_revision,
                        content: body.current_content,
                    });
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
            setDoc(updated);

            // If the user kept typing while the request was in flight, the
            // pending ref now holds newer content — keep it so the next debounce
            // fires another save. If it matches what we sent, clear it.
            if (pendingContentRef.current === sentContent) {
                pendingContentRef.current = null;
            }
            setStatus({ kind: 'saved', at: Date.now() });
        } catch (e) {
            setStatus({
                kind: 'error',
                message: e instanceof Error ? e.message : 'Network error',
            });
        } finally {
            inFlightRef.current = false;
            // If more input arrived during the save, schedule another flush.
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
            if (status.kind === 'too-large' || status.kind === 'conflict') {
                // Block autosaves while the user resolves the situation.
                return;
            }
            if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
            debounceTimerRef.current = setTimeout(persist, AUTOSAVE_DEBOUNCE_MS);
        },
        [persist, status.kind]
    );

    const handleReload = useCallback(async () => {
        try {
            setStatus({ kind: 'loading' });
            const response: DocumentResponse = await fetchFromApi(
                `/api/document/main?paper_id=${encodeURIComponent(paperId)}`
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
    }, [paperId]);

    // While the agentic chat is streaming, poll for `write_main_doc` results.
    // Skip the overwrite when the user has unsaved typing — let the 409 path
    // resolve the conflict instead of silently clobbering their keystrokes.
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
            // Skip while the user is actively saving — refetching mid-PUT
            // would race with the response body update.
            if (inFlightRef.current) return;
            try {
                const response: DocumentResponse = await fetchFromApi(
                    `/api/document/main?paper_id=${encodeURIComponent(paperId)}`
                );
                if (cancelled) return;
                if (response.revision <= current.revision) return;
                // Agent (or another tab) wrote. If the user has unsaved
                // typing, fall through to the existing 409 path on next
                // autosave instead of overwriting them.
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
        const startInterval = () => {
            if (timer) return;
            timer = setInterval(() => {
                if (agentStreamingRef.current) tick();
            }, AGENT_POLL_INTERVAL_MS);
        };
        startInterval();
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

    return (
        <div className="flex flex-col h-full">
            <div className="flex items-center justify-between border-b border-border px-2">
                <StatusRow status={status} />
                {status.kind === 'conflict' && (
                    <button
                        type="button"
                        onClick={handleReload}
                        className="text-xs px-2 py-1 rounded bg-amber-100 dark:bg-amber-950 hover:bg-amber-200 dark:hover:bg-amber-900 text-amber-900 dark:text-amber-100"
                    >
                        Reload
                    </button>
                )}
            </div>
            <div className={`flex-1 overflow-y-auto ${isMobile ? 'pb-24' : ''}`}>
                {doc ? (
                    <MilkdownImpl
                        initialContent={doc.content}
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
