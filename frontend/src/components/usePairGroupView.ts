'use client';
import type { PairFilter } from '@/lib/workflowContracts';
import { useStoredState } from './SessionCache';
import { normalizedPairFilter } from './usePairReviewCache';

type GroupView = { filter: PairFilter; offset: number; search: string };
export function usePairGroupView(run: string) {
  // Existing slots retain the current view across the four session URL tabs.
  const [storedFilter, setFilter] = useStoredState<PairFilter>(run, 'pairs:mvp-minimal-filter', {});
  const filter:PairFilter = {...storedFilter, ...(storedFilter.group==='BLOCKED'?{group:undefined,subgroup:undefined}:{}), visibility:'review',taxonomy:'mvp_v1'};
  const [offset, setOffset] = useStoredState(run, 'pairs:mvp-minimal-offset', 0);
  const [search, setSearch] = useStoredState(run, 'pairs:mvp-minimal-search', '');
  const [views, setViews] = useStoredState<Record<string, GroupView>>(run, 'pairs:mvp-minimal-group-views', {});
  function change(key: keyof PairFilter, value: string) {
    if (key === 'group') {
      if((filter.group || '')===value&&value)return false;
      setViews(previous => ({ ...previous, [filter.group || 'ALL']: { filter, offset, search } }));
      const next = views[value || 'ALL'];
      setFilter(normalizedPairFilter(value ? next?.filter || { group: value } : {}));
      setOffset(value ? next?.offset || 0 : 0);
      setSearch(value ? next?.search || '' : '');
    } else {
      if ((filter[key] || '') === value) return false;
      setFilter(normalizedPairFilter({ ...filter, [key]: value }));
      setOffset(0);
    }
    return true;
  }
  function showGroup(group: string) {
    setViews(previous => ({ ...previous, [filter.group || 'ALL']: { filter, offset, search } }));
    setFilter(normalizedPairFilter({ group }));
    setOffset(0);
    setSearch('');
  }
  function chooseSubgroup(group:string, subgroup:string) {
    setViews(previous => ({ ...previous, [filter.group || 'ALL']: { filter, offset, search } }));
    setFilter(normalizedPairFilter({group,subgroup,visibility:'review',taxonomy:'mvp_v1'}));
    setOffset(0);
    setSearch('');
  }
  const destination=(group:string)=>group===filter.group?{filter,offset,search}:views[group] || {filter:{group},offset:0,search:''};
  return { filter, offset, setOffset, search, setSearch, change, showGroup, chooseSubgroup, destination };
}
