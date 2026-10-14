'use client';
import { useCallback, useEffect, useMemo, useRef } from 'react';
import { hashKey, type QueryKey } from '@tanstack/react-query';
import { getPairs, getPairSummary } from '@/lib/workflowApi';
import type { PairFilter, PairRead, WorkflowRead } from '@/lib/workflowContracts';
import { sessionKey, useSessionCache } from './SessionCache';
import { useSessionRead } from './useSessionRead';

const FRESH_TIME = 120_000;
const BULK_REFRESH_INTERVAL = 5_000;
export function normalizedPairFilter(filter: PairFilter): PairFilter {
  return Object.fromEntries(Object.entries(filter).filter(([, value]) => !!value));
}
function pageOptions(user: string, run: string, filter: PairFilter, offset: number, revision: number, assessmentId?:string|null, adviceRevision?:string|null) {
  const normalized = normalizedPairFilter(filter);
  return {
    queryKey: sessionKey(user, run, 'pairs', normalized, { offset, limit: 10, ...(assessmentId?{assessment_id:assessmentId}:{}), ...(adviceRevision?{advice_revision:adviceRevision}:{}) }),
    staleTime: FRESH_TIME,
    queryFn: async ({ signal }: { signal: AbortSignal }) => ({ value: {
      ...await getPairs(run, new URLSearchParams({ ...normalized, offset: String(offset), limit: '10' }), signal),
      loadedOffset: offset, decisionRevision: revision, assessmentId, adviceRevision,
    } }),
  };
}

// Revisions describe freshness, not resource identity. Invalidate conservatively:
// negative links can affect eligibility outside the Group where a decision was made.
function useRevisionRefresh(run: string, resource: 'pairs' | 'pair-summary', revision: number, observed: number | undefined,
  read: { queryKey: QueryKey; fetching: boolean; previous: boolean; error: string }, allowed: boolean, bulk: boolean) {
  const { client, userId } = useSessionCache();
  const keyHash = hashKey(read.queryKey);
  const attempt = useRef<{ key: string; revision: number; at: number } | null>(null);
  useEffect(() => {
    if (!allowed) return;
    void client.invalidateQueries({ queryKey: sessionKey(userId, run, resource), refetchType: 'none', predicate: query => {
      const value = (query.state.data as { value?: { decisionRevision?: number; decision_revision?: number } } | undefined)?.value;
      return !!value && Number(resource === 'pairs' ? value.decisionRevision : value.decision_revision) < revision;
    } });
  }, [client, userId, run, resource, revision, allowed]);
  useEffect(() => {
    if (!allowed || observed === undefined || observed >= revision || read.previous || read.fetching || read.error) return;
    const updatedAt = client.getQueryState(read.queryKey)?.dataUpdatedAt || 0;
    const repeated = attempt.current?.key === keyHash && attempt.current.revision === revision;
    const lastRead = repeated ? Math.max(updatedAt, attempt.current!.at) : updatedAt;
    const delay = bulk || repeated ? Math.max(0, BULK_REFRESH_INTERVAL - (Date.now() - lastRead)) : 0;
    const timer = setTimeout(() => {
      attempt.current = { key: keyHash, revision, at: Date.now() };
      void client.invalidateQueries({ queryKey: read.queryKey, exact: true }, { cancelRefetch: false });
    }, delay);
    return () => clearTimeout(timer);
  }, [client, keyHash, revision, observed, read.fetching, read.previous, read.error, allowed, bulk]);
}

export function usePairReviewCache(workflow: WorkflowRead, filter: PairFilter, offset: number, enabled: boolean) {
  const cache = useSessionCache();
  const run = workflow.run_id, revision = workflow.decision_revision, assessmentId=workflow.assessment_id, adviceRevision=workflow.advice_revision;
  const normalized = useMemo(() => normalizedPairFilter(filter), [filter.group, filter.subgroup, filter.resolution]);
  const options = useMemo(() => pageOptions(cache.userId, run, normalized, offset, revision, assessmentId, adviceRevision), [cache.userId, run, normalized, offset, revision, assessmentId, adviceRevision]);
  const load = useCallback(async (signal: AbortSignal) => (await options.queryFn({ signal })).value, [options]);
  const read = useSessionRead(run, ['pairs', normalized], { offset, limit: 10, ...(assessmentId?{assessment_id:assessmentId}:{}), ...(adviceRevision?{advice_revision:adviceRevision}:{}) }, load, enabled, true, FRESH_TIME);
  const summaryLoad = useCallback(async (signal: AbortSignal) => {
    const result = await getPairSummary(run, signal);
    return { ...result, decision_revision: result.decision_revision ?? revision };
  }, [run, revision, assessmentId, adviceRevision]);
  const summary = useSessionRead(run, ['pair-summary'], assessmentId?{assessment_id:assessmentId}:{}, summaryLoad, enabled, true, FRESH_TIME);
  const allowed = enabled && !cache.blocked[run];
  const bulk = workflow.active_operation?.operation_type === 'BULK_DECISIONS';
  useRevisionRefresh(run, 'pairs', revision, read.data?.decisionRevision, read, allowed, bulk);
  useRevisionRefresh(run, 'pair-summary', revision, summary.data?.decision_revision, summary, allowed, bulk);

  const prefetchGroup = useCallback((group: string) => {
    if (!allowed || workflow.active_operation || !(summary.data?.groups.find(item => item.key === group)?.total)) return;
    void cache.client.prefetchQuery(pageOptions(cache.userId, run, { group, resolution:normalized.resolution }, 0, revision, assessmentId, adviceRevision));
  }, [allowed, workflow.active_operation, summary.data, cache.client, cache.userId, run, revision, assessmentId, adviceRevision, normalized.resolution]);
  useEffect(() => {
    if (!allowed || workflow.active_operation || !read.data || read.previous || read.fetching || read.error || !summary.data || read.data.decisionRevision !== revision) return;
    let stopped = false;
    const tasks: ReturnType<typeof pageOptions>[] = [];
    if (read.data.next_cursor && read.data.loadedOffset === offset) tasks.push(pageOptions(cache.userId, run, normalized, offset + 10, revision, assessmentId, adviceRevision));
    for (const group of summary.data.groups.filter(item => item.total > 0 && item.key !== normalized.group).slice(0, 3)) {
      tasks.push(pageOptions(cache.userId, run, { group: group.key, resolution:normalized.resolution }, 0, revision, assessmentId, adviceRevision));
    }
    const timer = setTimeout(async () => {
      for (const task of tasks) {
        if (stopped) return;
        // Already-read pages refresh lazily when visited. Revision invalidation
        // must not turn bounded first-visit prefetch into a fan-out after every write.
        if (cache.client.getQueryData(task.queryKey) !== undefined) continue;
        // fetchQuery populates the same cache but preserves the rejection.
        // prefetchQuery swallows errors; awaiting it could start another read
        // before React has unmounted this queue after a 401/403.
        try { await cache.client.fetchQuery(task); }
        catch { return; }
      }
    }, 250);
    return () => { stopped = true; clearTimeout(timer); };
  }, [allowed, workflow.active_operation, read.data, read.previous, read.fetching, read.error, summary.data, cache.client, cache.userId, run, normalized, offset, revision, assessmentId, adviceRevision]);

  const updatedPair = useCallback((pair: PairRead) => {
    cache.client.setQueriesData<{ value: Awaited<ReturnType<typeof load>> }>({ queryKey: sessionKey(cache.userId, run, 'pairs') }, previous => {
      if (!previous || !previous.value.items.some(item => item.pair_id === pair.pair_id && item.revision <= pair.revision)) return previous;
      return { value: { ...previous.value, items: previous.value.items.map(item => item.pair_id === pair.pair_id && item.revision <= pair.revision ? { ...item, ...pair, left_record: pair.left_record ?? item.left_record, right_record: pair.right_record ?? item.right_record } : item) } };
    });
    void cache.client.invalidateQueries({ queryKey: sessionKey(cache.userId, run, 'pairs') }, { cancelRefetch: false });
    void cache.client.invalidateQueries({ queryKey: sessionKey(cache.userId, run, 'pair-summary') }, { cancelRefetch: false });
  }, [cache.client, cache.userId, run]);
  return { read, summary, prefetchGroup, updatedPair, fresh: !!read.data && !read.previous && !read.error && read.data.decisionRevision === revision && read.data.assessmentId === assessmentId && read.data.adviceRevision === adviceRevision };
}
