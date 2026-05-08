import type {
    ChatMessage,
    PaperData,
    PaperHighlight,
    PaperHighlightAnnotation,
    PaperItem,
} from "@/lib/schema";

export type SyncStatus =
    | "idle"
    | "syncing"
    | "pending"
    | "paused"
    | "needs-attention"
    | "error";

export interface SyncState {
    id: "global";
    scope: "all" | "latest-50";
    lastSuccessfulSyncAt?: string;
    syncedPaperIds: string[];
    pinnedPaperIds: string[];
    pendingCount: number;
    status: SyncStatus;
    lastError?: string;
    updatedAt: string;
}

export interface CachedPaper {
    paperId: string;
    data: PaperData;
    pinned: boolean;
    lastOpenedAt: string;
    cachedAt: string;
}

export interface DocumentListSnapshot {
    paperId: string;
    documents: unknown[];
    cachedAt: string;
}

export interface DocumentSnapshot {
    documentId: string;
    paperId?: string | null;
    data: unknown;
    cachedAt: string;
}

export interface HighlightSnapshot {
    paperId: string;
    highlights: PaperHighlight[];
    cachedAt: string;
}

export interface AnnotationSnapshot {
    paperId: string;
    annotations: PaperHighlightAnnotation[];
    cachedAt: string;
}

export interface ChatSnapshot {
    paperId: string;
    messages: ChatMessage[];
    cachedAt: string;
}

export interface PdfMeta {
    paperId: string;
    blob: Blob;
    sourceUrl?: string;
    bytes: number;
    cachedAt: string;
}

export interface OutboxItem {
    id: string;
    type: "doc-put" | "highlight-create" | "highlight-update" | "highlight-delete" | "annotation-create" | "annotation-update" | "annotation-delete";
    paperId?: string;
    createdAt: string;
    updatedAt: string;
    payload:
        | DocPutPayload
        | HighlightCreatePayload
        | HighlightUpdatePayload
        | HighlightDeletePayload
        | AnnotationCreatePayload
        | AnnotationUpdatePayload
        | AnnotationDeletePayload;
    attempts: number;
    lastError?: string;
}

export interface DocPutPayload {
    documentId: string;
    content: string;
    expectedRevision: number;
}

export interface HighlightCreatePayload {
    localId: string;
    paperId: string;
    highlight: PaperHighlight;
}

export interface HighlightUpdatePayload {
    highlightId: string;
    highlight: PaperHighlight;
}

export interface HighlightDeletePayload {
    highlightId: string;
}

export interface AnnotationCreatePayload {
    localId: string;
    paperId: string;
    highlightId: string;
    content: string;
}

export interface AnnotationUpdatePayload {
    annotationId: string;
    content: string;
}

export interface AnnotationDeletePayload {
    annotationId: string;
}

export interface SyncPaperCandidate extends Pick<PaperItem, "id" | "created_at"> {
    last_accessed_at?: string | null;
}
