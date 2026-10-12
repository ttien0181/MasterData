'use client';
import { QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createContext, useContext, useEffect, useMemo, useState, useSyncExternalStore, type Dispatch, type ReactNode, type SetStateAction } from 'react';
import { ApiError } from '@/lib/api';

export const CACHE_LIFETIME = 30 * 60 * 1000;
type Entry = { value: unknown; touched: number };
export class SessionUIStore {
  private entries = new Map<string, Entry>();
  private listeners = new Set<() => void>();
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  read<T>(key: string, initial: T): T {
    const entry = this.entries.get(key);
    const draft = key.includes('golden-draft:') && !!(entry?.value as { draft?: unknown } | undefined)?.draft;
    if (entry && (draft || Date.now() - entry.touched < CACHE_LIFETIME)) { entry.touched = Date.now(); return entry.value as T; }
    this.entries.set(key, { value: initial, touched: Date.now() });
    return initial;
  }
  write<T>(key: string, value: T) {
    this.entries.set(key, { value, touched: Date.now() });
    // Expired inactive UI state is bounded; unsaved Golden drafts survive until discarded/logout.
    for (const [slot, entry] of this.entries) {
      if (Date.now() - entry.touched >= CACHE_LIFETIME && !(slot.includes('golden-draft:') && (entry.value as { draft?: unknown })?.draft)) this.entries.delete(slot);
    }
    this.listeners.forEach(listener => listener());
  }
  remove(prefix: string) { for (const key of this.entries.keys()) if (key.startsWith(prefix)) this.entries.delete(key); this.listeners.forEach(listener => listener()); }
  delete(key: string) { this.entries.delete(key); this.listeners.forEach(listener => listener()); }
  clear() { this.entries.clear(); }
}
const uiKey = (run: string, slot: string) => `${JSON.stringify(run)}:${slot}`;
type CacheContext = { client: QueryClient; ui: SessionUIStore; userId: string; blocked: Record<string, string>; retryAccess: (run: string) => void; handleError: (error: unknown, run: string, read?: boolean) => void };
const Context = createContext<CacheContext | null>(null);
export function useSessionCache() { const cache = useContext(Context); if (!cache) throw new Error('Session cache provider required'); return cache; }
export const sessionKey = (user: string, run: string, ...resource: unknown[]) => ['session', user, run, ...resource] as const;
export function SessionCacheProvider({ userId, children, onAuthLost }: { userId: string; children: ReactNode; onAuthLost?: () => void }) {
  return <CacheOwner key={userId} userId={userId} onAuthLost={onAuthLost}>{children}</CacheOwner>;
}
function CacheOwner({ userId, children, onAuthLost }: { userId: string; children: ReactNode; onAuthLost?: () => void }) {
  const [blocked, setBlocked] = useState<Record<string, string>>({});
  const [expired, setExpired] = useState(false);
  const [ui] = useState(() => new SessionUIStore());
  function handleError(error: unknown, run: string, read = false) {
    if (!(error instanceof ApiError)) return;
    if (error.status === 401) { setExpired(true); ui.clear(); client.clear(); onAuthLost?.(); }
    else if (read && error.status === 403) setBlocked(previous => ({ ...previous, [run]: 'Không còn quyền đọc phiên hoặc phiên không tồn tại.' }));
  }
  const [client] = useState(() => new QueryClient({
    queryCache: new QueryCache({ onError: (error, query) => {
      if (!(error instanceof ApiError)) return;
      const run = String(query.queryKey[2]);
      if (error.status === 404 && query.queryKey[3] === 'workflow') setBlocked(previous => ({ ...previous, [run]: 'Không còn quyền đọc phiên hoặc phiên không tồn tại.' }));
      else handleError(error, run, true);
    } }),
    defaultOptions: { queries: { staleTime: 60_000, gcTime: CACHE_LIFETIME, retry: false, refetchOnWindowFocus: true, refetchOnReconnect: true }, mutations: { retry: false } },
  }));
  useEffect(() => {
    for (const run of Object.keys(blocked)) {
      const key = sessionKey(userId, run);
      void client.cancelQueries({ queryKey: key });
      client.removeQueries({ queryKey: key });
      ui.remove(uiKey(run, ''));
    }
  }, [blocked, client, ui, userId]);
  useEffect(() => () => { client.clear(); ui.clear(); }, [client, ui]);
  const retryAccess = (run: string) => setBlocked(previous => { const next = { ...previous }; delete next[run]; return next; });
  if (expired) return <p role="alert">Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.</p>;
  return <Context.Provider value={{ client, ui, userId, blocked, retryAccess, handleError }}><QueryClientProvider client={client}>{children}</QueryClientProvider></Context.Provider>;
}
export function useStoredState<T>(run: string, slot: string, initial: T | (() => T)): [T, Dispatch<SetStateAction<T>>] {
  const cache = useContext(Context);
  const [local] = useState(() => new SessionUIStore());
  const store = cache?.ui || local;
  const key = uiKey(run, slot);
  // Like useState, the initializer is used only for a new identity.
  const seed = useMemo(() => typeof initial === 'function' ? (initial as () => T)() : initial, [key]);
  const value = useSyncExternalStore(store.subscribe, () => store.read(key, seed), () => seed);
  const setValue = useMemo<Dispatch<SetStateAction<T>>>(() => next => {
    const current = store.read(key, seed);
    store.write(key, typeof next === 'function' ? (next as (previous: T) => T)(current) : next);
  }, [store, key, seed]);
  return [value, setValue];
}
export function removeStoredState(cache: CacheContext, run: string, slot: string) { cache.ui.delete(uiKey(run, slot)); }
