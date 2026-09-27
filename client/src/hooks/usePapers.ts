import useSWR, { mutate as mutateGlobal } from 'swr';
import { toast } from 'sonner';
import { api, unwrap } from '@/lib/api/client';
import { LibraryPaper } from '@/lib/schema';

interface UserPapersProps {
	detailed?: boolean;
	/** The archived papers instead of the library. */
	archived?: boolean;
	/** Don't fetch (e.g. the archived count where there's no archive view). */
	skip?: boolean;
}

export function usePapers({ detailed = false, archived = false, skip = false }: UserPapersProps = {}) {
	const { data, error, isLoading, mutate } = useSWR(
		skip ? null : ['/api/paper/all', detailed, archived],
		async () => {
			const { papers } = await unwrap(api.GET('/api/paper/all', { params: { query: { detailed, archived } } }));
			return papers;
		},
	);

	const setPapers = (paperId: string, updatedPaper: LibraryPaper) => {
		if (data) {
			const updatedPapers = data.map(p => (p.id === paperId ? updatedPaper : p));
			mutate(updatedPapers, false); // Update local data without revalidating
		}
	};

	return {
		papers: data,
		error,
		isLoading,
		setPapers,
		mutate,
	};
}

/** The lists archiving changes: library (both views), home, ⌘K. */
const PAPER_LIST_KEYS = ['/api/paper/all', '/api/paper/active', '/api/paper/relevant'];

export function revalidatePaperLists() {
	return mutateGlobal((key) => Array.isArray(key) && PAPER_LIST_KEYS.includes(key[0]));
}

/** Archive or unarchive papers, then refresh every paper list. */
export async function setPapersArchived(paperIds: string[], archived: boolean) {
	try {
		return await unwrap(api.POST('/api/paper/archive', { body: { paper_ids: paperIds, archived } }));
	} finally {
		void revalidatePaperLists();
	}
}

/**
 * Archive (or unarchive) with a toast; archiving offers an Undo. `onChange`
 * runs after the request and after an undo, with the papers' new state.
 * Resolves false (after an error toast) when the request fails.
 */
export async function archivePapersWithToast(
	paperIds: string[],
	archived: boolean,
	onChange?: (archived: boolean) => void,
): Promise<boolean> {
	const count = paperIds.length;
	const noun = count === 1 ? 'Paper' : `${count} papers`;
	try {
		await setPapersArchived(paperIds, archived);
	} catch (error) {
		console.error('Failed to update archive state', error);
		toast.error(archived ? `Couldn't archive ${noun.toLowerCase()}.` : `Couldn't unarchive ${noun.toLowerCase()}.`);
		return false;
	}
	onChange?.(archived);
	if (!archived) {
		toast.success(`${noun} moved back to your library`);
		return true;
	}
	toast.success(`${noun} archived`, {
		action: {
			label: 'Undo',
			onClick: () => {
				setPapersArchived(paperIds, false)
					.then(() => onChange?.(false))
					.catch(() => toast.error(`Couldn't unarchive ${noun.toLowerCase()}.`));
			},
		},
	});
	return true;
}
