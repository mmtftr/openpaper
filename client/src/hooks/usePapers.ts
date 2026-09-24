import useSWR from 'swr';
import { api, unwrap } from '@/lib/api/client';
import { LibraryPaper } from '@/lib/schema';

interface UserPapersProps {
	detailed?: boolean;
}

export function usePapers({ detailed = false }: UserPapersProps = {}) {
	const { data, error, isLoading, mutate } = useSWR(
		['/api/paper/all', detailed],
		async () => {
			const { papers } = await unwrap(api.GET('/api/paper/all', { params: { query: { detailed } } }));
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
