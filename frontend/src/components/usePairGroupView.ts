'use client';
import type { PairFilter } from '@/lib/workflowContracts';
import { useStoredState } from './SessionCache';
import { normalizedPairFilter } from './usePairReviewCache';

type GroupView = { filter: PairFilter; offset: number; search: string };
export function usePairGroupView(run: string) {
  // Existing slots retain the current view across the four session URL tabs.
  const [filter, setFilter] = useStoredState<PairFilter>(run, 'pairs:filter', {resolution:'REQUIRED'});
  const [offset, setOffset] = useStoredState(run, 'pairs:offset', 0);
  const [search, setSearch] = useStoredState(run, 'pairs:search', '');
  const [views, setViews] = useStoredState<Record<string, GroupView>>(run, 'pairs:group-views', {});
  function change(key: keyof PairFilter, value: string) {
    if ((filter[key] || '') === value) return false;
    if (key === 'group') {
      setViews(previous => ({ ...previous, [filter.group || 'ALL']: { filter, offset, search } }));
      const next = views[value || 'ALL'];
      setFilter(normalizedPairFilter(next?.filter || { group: value, resolution:filter.resolution }));
      setOffset(next?.offset || 0);
      setSearch(next?.search || '');
    } else {
      setFilter(normalizedPairFilter({ ...filter, [key]: value }));
      setOffset(0);
    }
    return true;
  }
  return { filter, offset, setOffset, search, setSearch, change };
}
