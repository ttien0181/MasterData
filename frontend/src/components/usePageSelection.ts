'use client';
import { createContext, useContext, useEffect, useRef, useState } from 'react';
import type { BulkScope, PairFilter } from '@/lib/workflowContracts';
type Stored = { key: string; ids: Set<string>; all: boolean };
export const PageSelectionContext = createContext<Map<string, Stored> | null>(null);
export function usePageSelection(filter: PairFilter, identity = '', namespace = 'pairs') {
  const cache = useContext(PageSelectionContext); const key = identity + JSON.stringify(filter);
  const prior = cache?.get(namespace); const initial = prior?.key === key ? prior : null;
  const [ids, setIds] = useState<Set<string>>(initial?.ids || new Set()); const [all, setAll] = useState(initial?.all || false);
  const previousKey = useRef(key);
  useEffect(() => { if (previousKey.current !== key) { setIds(new Set()); setAll(false); previousKey.current = key; cache?.delete(namespace); } }, [key, cache, namespace]);
  useEffect(() => { cache?.set(namespace, { key, ids, all }); }, [cache, namespace, key, ids, all]);
  const selected = (id: string) => all ? !ids.has(id) : ids.has(id);
  const toggle = (id: string) => setIds(previous => { const next = new Set(previous); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const clear = () => { setIds(new Set()); setAll(false); };
  const scope = (): BulkScope => all ? { filter, excluded_pair_ids: [...ids] } : { pair_ids: [...ids] };
  return { selected, toggle, clear, all, count: ids.size, ids: [...ids], scope,
    selectAll: () => { setAll(true); setIds(new Set()); },
    selectPage: (page: string[]) => setIds(previous => { const next = new Set(previous); page.forEach(id => all ? next.delete(id) : next.add(id)); return next; }),
    deselectPage: (page: string[]) => setIds(previous => { const next = new Set(previous); page.forEach(id => all ? next.add(id) : next.delete(id)); return next; }) };
}
