import useSWR from 'swr';
import { api, unwrap } from '@/lib/api/client';
import { PaperItem } from '@/lib/schema';

interface UserPapersProps {
	detailed?: boolean;
}

export function usePapers({ detailed = false }: UserPapersProps = {}) {
	const { data, error, isLoading, mutate } = useSWR<PaperItem[]>(
		['/api/paper/all', detailed],
		async () => {
			const { papers } = await unwrap(api.GET('/api/paper/all', { params: { query: { detailed } } }));
			// Compat: callers still hold the hand-written `PaperItem`.
			return papers as PaperItem[];
		},
	);

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
