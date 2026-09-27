// After a redeploy, a tab that is still open (Safari restores tabs for days)
// runs the old build, whose lazily loaded chunks no longer exist on the server:
// opening a panel then fails with "Loading chunk N failed". Re-rendering can't
// fix that; only a full reload (which fetches the new build) can.

const RELOAD_KEY = "openpaper:stale-deploy-reload-at";

export function isChunkLoadError(error: unknown): boolean {
	if (!(error instanceof Error)) return false;
	return (
		error.name === "ChunkLoadError" ||
		/Loading (CSS )?chunk \S+ failed|Importing a module script failed|Failed to fetch dynamically imported module|error loading dynamically imported module/i.test(
			error.message,
		)
	);
}

/**
 * Reload once for a chunk load error. Returns false (and does nothing) if we
 * already reloaded in the last 30s, so a chunk that is genuinely missing shows
 * the error instead of looping.
 */
export function reloadForStaleDeploy(): boolean {
	try {
		const last = Number(sessionStorage.getItem(RELOAD_KEY) || 0);
		if (Date.now() - last < 30_000) return false;
		sessionStorage.setItem(RELOAD_KEY, String(Date.now()));
	} catch {
		return false; // no sessionStorage = no loop guard; let the user reload
	}
	window.location.reload();
	return true;
}
