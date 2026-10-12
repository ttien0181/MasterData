'use client';
import { hashKey, useQuery, type QueryKey } from '@tanstack/react-query';
import { sessionKey, useSessionCache } from './SessionCache';
import { ApiError } from '@/lib/api';
type Cached<T> = { value: T };

export function useSessionRead<T>(run: string, identity: unknown[], params: unknown, load: (signal: AbortSignal) => Promise<T>, enabled = true, keepPrevious = true, staleTime = 60_000) {
  const cache = useSessionCache();
  const key = sessionKey(cache.userId, run, ...identity, params);
  const scope = hashKey(key.slice(0, -1));
  const sameScope = (other: QueryKey) => hashKey(other.slice(0, -1)) === scope;
  const recent = () => cache.client.getQueryCache().getAll().filter(query => sameScope(query.queryKey) && query.state.data !== undefined)
    .sort((a, b) => b.state.dataUpdatedAt - a.state.dataUpdatedAt)[0]?.state.data as Cached<T> | undefined;
  const allowed = enabled && !cache.blocked[run];
  const query = useQuery<Cached<T>, Error>({
    queryKey: key, queryFn: async ({ signal }) => ({ value: await load(signal) }), enabled: allowed, staleTime,
    placeholderData: keepPrevious ? (previous, previousQuery) => previous && previousQuery && sameScope(previousQuery.queryKey) ? previous : recent() : undefined,
  });
  const fallback = allowed && keepPrevious && query.isError ? recent() : undefined;
  const denied = query.error instanceof ApiError && [401, 403].includes(query.error.status);
  return { data: allowed && !denied ? query.data?.value ?? fallback?.value ?? null : null, error: cache.blocked[run] || query.error?.message || '', cause: query.error,
    retry: () => { if (cache.blocked[run]) cache.retryAccess(run); else void query.refetch(); },
    fetching: query.isFetching, previous: query.isPlaceholderData || !!fallback, queryKey: key,
  };
}
