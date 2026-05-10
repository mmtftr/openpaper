import useSWR from 'swr';
import { fetchFromApi } from '@/lib/api';
import { readCachedPaperList, writeCachedPaperList } from '@/lib/offline';
import { PaperItem } from '@/lib/schema';

const fetcher = async (url: string): Promise<PaperItem[]> => {
	try {
		const data = await fetchFromApi(url);
		const papers: PaperItem[] = data.papers || data;
		writeCachedPaperList(url, papers);
		return papers;
	} catch (err) {
		// Network failure: fall back to whatever we cached on the previous
		// successful load so the library page still renders offline.
		const cached = readCachedPaperList(url);
		if (cached) return cached;
		throw err;
	}
};

interface UserPapersProps {
	detailed?: boolean;
}

export function usePapers({ detailed = false }: UserPapersProps = {}) {
	const url = detailed ? '/api/paper/all?detailed=true' : '/api/paper/all';
	const { data, error, isLoading, mutate } = useSWR<PaperItem[]>(url, fetcher, {
		// Seed SWR's cache from localStorage on first render so the list is
		// visible before the (potentially failing) network request returns.
		fallbackData: readCachedPaperList(url) || undefined,
	});

	const setPapers = (paperId: string, updatedPaper: PaperItem) => {
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
