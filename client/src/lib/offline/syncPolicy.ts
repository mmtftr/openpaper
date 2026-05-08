import type { SyncPaperCandidate } from "./types";

const DEFAULT_RECENT_LIMIT = 50;

function timestampForPaper(paper: SyncPaperCandidate) {
    const value = paper.last_accessed_at || paper.created_at || "";
    const timestamp = Date.parse(value);
    return Number.isFinite(timestamp) ? timestamp : 0;
}

export function getSyncPaperIds(
    papers: SyncPaperCandidate[],
    pinnedPaperIds: string[] = [],
    recentLimit = DEFAULT_RECENT_LIMIT
) {
    const pinned = new Set(pinnedPaperIds);
    const scope = papers.length < recentLimit ? "all" as const : "latest-50" as const;

    const baseIds =
        scope === "all"
            ? papers.map((paper) => paper.id)
            : [...papers]
                .sort((a, b) => timestampForPaper(b) - timestampForPaper(a))
                .slice(0, recentLimit)
                .map((paper) => paper.id);

    return {
        scope,
        paperIds: Array.from(new Set([...baseIds, ...pinned])),
    };
}
