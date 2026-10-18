import type { ExplanationRead } from './workflowApi';

const MAX_EVENT = 128 * 1024;
const MAX_STREAM = 1024 * 1024;
const invalid = () => new Error('Master Bot stream is incomplete or invalid.');
export function isExplanationResult(value: unknown): value is ExplanationRead {
  if (!value || typeof value !== 'object') return false;
  const result = value as ExplanationRead;
  if (!['READY', 'TEMPLATE', 'STALE'].includes(result.status)) return false;
  if (result.explanation === null) return result.status !== 'READY';
  const explanation = result.explanation;
  return !!explanation && typeof explanation.summary_vi === 'string' &&
    (result.status !== 'READY' || !!explanation.summary_vi.trim()) &&
    Array.isArray(explanation.limitations) && explanation.limitations.every(v => typeof v === 'string') &&
    (explanation.attention_fields === undefined || Array.isArray(explanation.attention_fields) && explanation.attention_fields.every(v => typeof v === 'string'));
}

export async function readExplanationStream(response: Response, onDelta: (text: string) => void, signal?: AbortSignal): Promise<ExplanationRead> {
  if (!response.headers.get('Content-Type')?.toLowerCase().startsWith('text/event-stream') || !response.body) throw invalid();
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let line = '', event = '', data: string[] = [], eventSize = 0, total = 0, skipLF = false;
  let result: ExplanationRead | undefined;
  const abort = () => { void reader.cancel().catch(() => undefined); };
  signal?.addEventListener('abort', abort, { once: true });
  function consumeLine() {
    if (!line) {
      if (event === 'delta' || event === 'result') {
        const value: unknown = JSON.parse(data.join('\n'));
        if (event === 'result') {
          if (!isExplanationResult(value)) throw invalid();
          result = value;
        } else {
          if (!value || typeof value !== 'object' || typeof (value as {text?: unknown}).text !== 'string') throw invalid();
          onDelta((value as {text: string}).text);
        }
      }
      event = ''; data = []; eventSize = 0;
    } else if (!line.startsWith(':')) {
      const colon = line.indexOf(':');
      const field = colon < 0 ? line : line.slice(0, colon);
      const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '');
      if (field === 'event') event = value;
      if (field === 'data') data.push(value);
    }
    line = '';
  }
  try {
    while (true) {
      signal?.throwIfAborted();
      const { value, done } = await reader.read();
      signal?.throwIfAborted();
      const text = decoder.decode(value, { stream: !done });
      total += value?.byteLength ?? 0;
      if (total > MAX_STREAM) throw invalid();
      for (const char of text) {
        if (skipLF && char === '\n') { skipLF = false; continue; }
        skipLF = false;
        if (++eventSize > MAX_EVENT) throw invalid();
        if (char === '\r' || char === '\n') { consumeLine(); skipLF = char === '\r'; }
        else line += char;
        if (result) return result;
      }
      if (done) throw invalid();
    }
  } finally {
    signal?.removeEventListener('abort', abort);
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
