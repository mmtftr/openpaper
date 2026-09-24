import useSWR from 'swr';
import { api, unwrap } from '@/lib/api/client';
import { ModelSlot, ModelSlotUpdate } from '@/lib/schema';

/** Settings -> Models: every model slot plus the selectable models. */
export function useModelSettings() {
	const { data, error, isLoading, mutate } = useSWR(
		'/api/settings/models',
		() => unwrap(api.GET('/api/settings/models')),
	);

	/** Save a slot's override (all fields null = back to the default). */
	const updateSlot = async (slot: string, update: ModelSlotUpdate): Promise<ModelSlot> => {
		const updated = await unwrap(
			api.PUT('/api/settings/models/{slot}', { params: { path: { slot } }, body: update }),
		);
		await mutate(
			current =>
				current && {
					...current,
					slots: current.slots.map(s => (s.slot === slot ? updated : s)),
				},
			{ revalidate: false },
		);
		return updated;
	};

	return { settings: data, error, isLoading, updateSlot };
}
