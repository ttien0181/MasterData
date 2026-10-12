'use client';
import { useEffect, useState } from 'react';
export function useWorkflowRead<T>(load: (signal: AbortSignal) => Promise<T>, enabled = true, keepPrevious = false) {
  const [data, setData] = useState<T | null>(null); const [error, setError] = useState(''); const [attempt, setAttempt] = useState(0);
  useEffect(() => { if (!enabled) return; const c = new AbortController(); setError(''); if (!keepPrevious) setData(null);
    void load(c.signal).then(value => { if (!c.signal.aborted) setData(value); }).catch(e => { if (!c.signal.aborted) setError(e.message); });
    return () => c.abort();
  }, [load, enabled, attempt, keepPrevious]);
  return { data, error, retry: () => setAttempt(n => n + 1) };
}
export function ReadNotice({ error, retry }: { error: string; retry: () => void }) { return <p role={error ? 'alert' : 'status'}>{error || 'Đang đọc dữ liệu…'}{error && <button onClick={retry}>Thử đọc lại</button>}</p>; }
export function CachedReadStatus({ read }: { read: { data: unknown; error: string; fetching: boolean; previous: boolean; retry: () => void } }) {
  if (!read.data) return null;
  return <>{read.previous && <p role="status">{read.fetching ? 'Đang tải trang đã chọn… ' : ''}Đang hiển thị trang đã đọc trước đó.</p>}{read.error && <ReadNotice error={read.error} retry={read.retry} />}</>;
}
export function Pager({ cursor, next, onPage, busy = false }: { cursor: string | null; next: string | null; onPage: (cursor: string | null) => void; busy?: boolean }) { return <div className="workflow-toolbar"><button disabled={!cursor || busy} onClick={() => onPage(null)}>Trang đầu</button><button disabled={!next || busy} onClick={() => onPage(next)}>Trang tiếp</button></div>; }
