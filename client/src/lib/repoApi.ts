import { z } from "zod";

import { API_BASE_URL } from "@/lib/api";

/**
 * Typed client for the repo-inspection endpoints
 * (`/api/paper/{id}/repo{,/tree,/file}`).
 *
 * Responses are parsed with lenient zod schemas: unknown keys pass through
 * untouched, but a shape that can't be used at all fails loudly at the
 * boundary instead of blowing up deep inside a component.
 */

export type RepoStatus = "pending" | "ingesting" | "ready" | "error";

const repoStatusSchema = z
    .enum(["pending", "ingesting", "ready", "error"])
    .catch("pending");

const paperRepoSchema = z.looseObject({
    status: repoStatusSchema,
    owner: z.string(),
    repo: z.string(),
    ref: z.string().nullish().transform((v) => v ?? ""),
    commit_sha: z.string().nullish().transform((v) => v ?? null),
    error: z.string().nullish().transform((v) => v ?? null),
    file_count: z.number().nullish().transform((v) => v ?? null),
    total_bytes: z.number().nullish().transform((v) => v ?? null),
    updated_at: z.string().nullish().transform((v) => v ?? ""),
});

export type PaperRepo = z.infer<typeof paperRepoSchema>;

const repoTreeFileSchema = z.looseObject({
    path: z.string(),
    size: z.number().nullish().transform((v) => v ?? 0),
});

export type RepoTreeFile = z.infer<typeof repoTreeFileSchema>;

const repoTreeSchema = z.looseObject({
    owner: z.string().nullish().transform((v) => v ?? ""),
    repo: z.string().nullish().transform((v) => v ?? ""),
    ref: z.string().nullish().transform((v) => v ?? ""),
    commit_sha: z.string().nullish().transform((v) => v ?? null),
    files: z.array(repoTreeFileSchema).nullish().transform((v) => v ?? []),
});

export type RepoTree = z.infer<typeof repoTreeSchema>;

const repoFileSchema = z.looseObject({
    path: z.string(),
    content: z.string().nullish().transform((v) => v ?? ""),
    size: z.number().nullish().transform((v) => v ?? 0),
    github_url: z.string().nullish().transform((v) => v ?? null),
});

export type RepoFile = z.infer<typeof repoFileSchema>;

/** Files past this size render as plain text — shiki on a 200KB blob janks. */
export const PLAIN_TEXT_BYTE_THRESHOLD = 200 * 1024;

const GITHUB_URL_PATTERN =
    /^(?:https?:\/\/)?(?:www\.)?github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/?(?:[#?].*)?$/;

/** Cheap client-side gate so obvious typos don't need a server round-trip. */
export function looksLikeGithubRepoUrl(url: string): boolean {
    return GITHUB_URL_PATTERN.test(url.trim());
}

/**
 * Error from a repo endpoint, carrying the HTTP status. The UI branches on
 * `status` (404 = "not connected" / "not in the snapshot") — `fetchFromApi`
 * only surfaces the server's `detail` string, which can't be branched on.
 */
export class RepoApiError extends Error {
    readonly status: number;

    constructor(message: string, status: number) {
        super(message);
        this.name = "RepoApiError";
        this.status = status;
    }
}

export function isRepoApiStatus(error: unknown, status: number): boolean {
    return error instanceof RepoApiError && error.status === status;
}

/** fetch + the codebase's error-detail extraction, keeping the status code. */
async function repoRequest(
    path: string,
    options: RequestInit = {}
): Promise<unknown> {
    const response = await fetch(`${API_BASE_URL}${path}`, {
        ...options,
        headers: {
            ...(options.body ? { "Content-Type": "application/json" } : {}),
            ...options.headers,
        },
        credentials: "include",
    });

    if (!response.ok) {
        let message = `API error: ${response.status}`;
        try {
            const body = await response.json();
            const detail = body?.detail ?? body?.message ?? body?.error;
            if (detail) {
                message =
                    typeof detail === "string" ? detail : JSON.stringify(detail);
            }
        } catch {
            message = `API error: ${response.status} ${response.statusText}`;
        }
        throw new RepoApiError(message, response.status);
    }

    if (response.status === 204) return null;
    return response.json();
}

/**
 * Repo row for a paper, or `null` when no repo is connected (404). Any other
 * failure throws.
 */
export async function getPaperRepo(paperId: string): Promise<PaperRepo | null> {
    try {
        const response = await repoRequest(
            `/api/paper/${encodeURIComponent(paperId)}/repo`
        );
        return paperRepoSchema.parse(response);
    } catch (error) {
        // "No repo connected" is a normal state, not a failure to surface.
        if (isRepoApiStatus(error, 404)) return null;
        throw error;
    }
}

/** Connect a GitHub repo and kick ingestion. 409 / 422 surface as errors. */
export async function connectPaperRepo(
    paperId: string,
    url: string
): Promise<PaperRepo> {
    const response = await repoRequest(
        `/api/paper/${encodeURIComponent(paperId)}/repo`,
        { method: "POST", body: JSON.stringify({ url }) }
    );
    return paperRepoSchema.parse(response);
}

export async function disconnectPaperRepo(paperId: string): Promise<void> {
    await repoRequest(`/api/paper/${encodeURIComponent(paperId)}/repo`, {
        method: "DELETE",
    });
}

export async function getRepoTree(paperId: string): Promise<RepoTree> {
    const response = await repoRequest(
        `/api/paper/${encodeURIComponent(paperId)}/repo/tree`
    );
    return repoTreeSchema.parse(response);
}

export async function getRepoFile(
    paperId: string,
    path: string
): Promise<RepoFile> {
    const response = await repoRequest(
        `/api/paper/${encodeURIComponent(paperId)}/repo/file?path=${encodeURIComponent(path)}`
    );
    return repoFileSchema.parse(response);
}

/**
 * Per-file cache shared by the viewer and the inline citation snippets: one
 * answer can cite the same file five times, and each snippet needs the real
 * source lines. Keyed by paper + path; in-flight promises are shared, and a
 * rejection is evicted so the next caller retries.
 */
const fileCache = new Map<string, Promise<RepoFile>>();

export function getRepoFileCached(
    paperId: string,
    path: string
): Promise<RepoFile> {
    const key = `${paperId}:${path}`;
    const cached = fileCache.get(key);
    if (cached) return cached;
    const request = getRepoFile(paperId, path).catch((error) => {
        fileCache.delete(key);
        throw error;
    });
    fileCache.set(key, request);
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
