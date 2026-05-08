import { openDB, type DBSchema, type IDBPDatabase } from "idb";

import type {
    AnnotationSnapshot,
    CachedPaper,
    ChatSnapshot,
    DocumentListSnapshot,
    DocumentSnapshot,
    HighlightSnapshot,
    OutboxItem,
    PdfMeta,
    SyncState,
} from "./types";

interface OpenPaperOfflineDB extends DBSchema {
    papers: {
        key: string;
        value: CachedPaper;
        indexes: { "by-last-opened": string };
    };
    documentLists: {
        key: string;
        value: DocumentListSnapshot;
    };
    documents: {
        key: string;
        value: DocumentSnapshot;
        indexes: { "by-paper": string };
    };
    highlights: {
        key: string;
        value: HighlightSnapshot;
    };
    annotations: {
        key: string;
        value: AnnotationSnapshot;
    };
    chatSnapshots: {
        key: string;
        value: ChatSnapshot;
    };
    outbox: {
        key: string;
        value: OutboxItem;
        indexes: { "by-created": string; "by-paper": string };
    };
    pdfMeta: {
        key: string;
        value: PdfMeta;
        indexes: { "by-cached-at": string };
    };
    syncState: {
        key: string;
        value: SyncState;
    };
}

const DB_NAME = "openpaper-offline";
const DB_VERSION = 1;

let dbPromise: Promise<IDBPDatabase<OpenPaperOfflineDB>> | null = null;

export function getOfflineDb() {
    if (typeof window === "undefined") {
        throw new Error("Offline database is only available in the browser");
    }

    dbPromise ??= openDB<OpenPaperOfflineDB>(DB_NAME, DB_VERSION, {
        upgrade(db) {
            const papers = db.createObjectStore("papers", { keyPath: "paperId" });
            papers.createIndex("by-last-opened", "lastOpenedAt");

            db.createObjectStore("documentLists", { keyPath: "paperId" });

            const documents = db.createObjectStore("documents", { keyPath: "documentId" });
            documents.createIndex("by-paper", "paperId");

            db.createObjectStore("highlights", { keyPath: "paperId" });
            db.createObjectStore("annotations", { keyPath: "paperId" });
            db.createObjectStore("chatSnapshots", { keyPath: "paperId" });

            const outbox = db.createObjectStore("outbox", { keyPath: "id" });
            outbox.createIndex("by-created", "createdAt");
            outbox.createIndex("by-paper", "paperId");

            const pdfMeta = db.createObjectStore("pdfMeta", { keyPath: "paperId" });
            pdfMeta.createIndex("by-cached-at", "cachedAt");

            db.createObjectStore("syncState", { keyPath: "id" });
        },
    });

    return dbPromise;
}

export async function getDefaultSyncState(): Promise<SyncState> {
    const db = await getOfflineDb();
    const existing = await db.get("syncState", "global");
    if (existing) return existing;

    const initial: SyncState = {
        id: "global",
        scope: "all",
        syncedPaperIds: [],
        pinnedPaperIds: [],
        pendingCount: 0,
        status: "idle",
        updatedAt: new Date().toISOString(),
    };
    await db.put("syncState", initial);
    return initial;
}
