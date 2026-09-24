import { api, ApiRequestError, unwrap, type Schemas } from "@/lib/api/client";

/**
 * Typed calls for the repo-inspection endpoints
 * (`/api/paper/{id}/repo{,/tree,/file}`).
 */

export type RepoStatus = Schemas["RepoStatus"];
export type PaperRepo = Schemas["RepoStatusResponse"];
export type RepoTreeFile = Schemas["RepoTreeFile"];
export type RepoTree = Schemas["RepoTreeResponse"];
export type RepoFile = Schemas["RepoFileResponse"];

/** Files past this size render as plain text — shiki on a 200KB blob janks. */
export const PLAIN_TEXT_BYTE_THRESHOLD = 200 * 1024;

const GITHUB_URL_PATTERN =
    /^(?:https?:\/\/)?(?:www\.)?github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/?(?:[#?].*)?$/;

/** Cheap client-side gate so obvious typos don't need a server round-trip. */
export function looksLikeGithubRepoUrl(url: string): boolean {
    return GITHUB_URL_PATTERN.test(url.trim());
}

/**
 * True when a repo call failed with this HTTP status. The UI branches on it
 * (404 = "not connected" / "not in the snapshot").
 */
export function isRepoApiStatus(error: unknown, status: number): boolean {
    return error instanceof ApiRequestError && error.status === status;
}

/**
 * Repo row for a paper, or `null` when no repo is connected (404). Any other
 * failure throws.
 */
export async function getPaperRepo(paperId: string): Promise<PaperRepo | null> {
    try {
        return await unwrap(
            api.GET("/api/paper/{paper_id}/repo", {
                params: { path: { paper_id: paperId } },
            })
        );
    } catch (error) {
        // "No repo connected" is a normal state, not a failure to surface.
        if (isRepoApiStatus(error, 404)) return null;
        throw error;
    }
}

/** Connect a GitHub repo and kick ingestion. 409 / 422 surface as errors. */
export function connectPaperRepo(paperId: string, url: string): Promise<PaperRepo> {
    return unwrap(
        api.POST("/api/paper/{paper_id}/repo", {
            params: { path: { paper_id: paperId } },
            body: { url },
        })
    );
}

export async function disconnectPaperRepo(paperId: string): Promise<void> {
    await unwrap(
        api.DELETE("/api/paper/{paper_id}/repo", {
            params: { path: { paper_id: paperId } },
        })
    );
}

export function getRepoTree(paperId: string): Promise<RepoTree> {
    return unwrap(
        api.GET("/api/paper/{paper_id}/repo/tree", {
            params: { path: { paper_id: paperId } },
        })
    );
}

export function getRepoFile(paperId: string, path: string): Promise<RepoFile> {
    return unwrap(
        api.GET("/api/paper/{paper_id}/repo/file", {
            params: { path: { paper_id: paperId }, query: { path } },
        })
    );
}

/**
 * Per-file cache shared by the viewer and the inline citation snippets: one
 * answer can cite the same file five times, and each snippet needs the real
 * source lines. Keyed by paper + path; in-flight promises are shared, and a
 * rejection is evicted so the next caller retries.
 *
 * Bounded LRU: a Map iterates in insertion order, a hit re-inserts its entry,
 * and the oldest entries are evicted past the cap. Each file is at most
 * 512 KB on the wire, so the cache can never hold more than ~20 MB.
 */
const fileCache = new Map<string, Promise<RepoFile>>();
const FILE_CACHE_MAX_ENTRIES = 40;

export function getRepoFileCached(
    paperId: string,
    path: string
): Promise<RepoFile> {
    const key = `${paperId}:${path}`;
    const cached = fileCache.get(key);
    if (cached) {
        fileCache.delete(key);
        fileCache.set(key, cached);
        return cached;
    }
    const request = getRepoFile(paperId, path).catch((error) => {
        fileCache.delete(key);
        throw error;
    });
    fileCache.set(key, request);
    while (fileCache.size > FILE_CACHE_MAX_ENTRIES) {
        const oldest = fileCache.keys().next().value;
        if (oldest === undefined) break;
        fileCache.delete(oldest);
    }
    return request;
}

/** Drop cached files — the snapshot changed (connect / disconnect / retry). */
export function clearRepoFileCache(paperId?: string): void {
    if (!paperId) {
        fileCache.clear();
        return;
    }
    const prefix = `${paperId}:`;
    for (const key of Array.from(fileCache.keys())) {
        if (key.startsWith(prefix)) fileCache.delete(key);
    }
}

export function formatBytes(bytes: number | null | undefined): string {
    if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return "";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
