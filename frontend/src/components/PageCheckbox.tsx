'use client';
import { useEffect, useRef } from 'react';
export default function PageCheckbox({ ids, selected, onSelect, onDeselect }: { ids: string[]; selected: (id: string) => boolean; onSelect: (ids: string[]) => void; onDeselect: (ids: string[]) => void }) {
  const ref = useRef<HTMLInputElement>(null); const count = ids.filter(selected).length;
  useEffect(() => { if (ref.current) ref.current.indeterminate = count > 0 && count < ids.length; }, [count, ids.length]);
  return <input ref={ref} type="checkbox" aria-label="Chọn trang hiện tại" checked={ids.length > 0 && count === ids.length} disabled={!ids.length} onChange={e => e.target.checked ? onSelect(ids) : onDeselect(ids)} />;
}
