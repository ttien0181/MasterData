import { afterEach, expect, it, vi } from 'vitest';
import { setCsrfToken } from './api';
import { streamWorkflowPairExplanation } from './workflowApi';

afterEach(()=>vi.unstubAllGlobals());
const body={request_id:'req',expected_workflow_revision:3};
const result={status:'READY',explanation:{summary_vi:'Tên giống.',limitations:[]}};
const event=(name:string,value:unknown)=>`event: ${name}\r\ndata: ${JSON.stringify(value)}\r\n\r\n`;
it('delivers actual deltas before final, preserving split UTF8, SSE and mutation authentication',async()=>{
 let controller!:ReadableStreamDefaultController<Uint8Array>;
 const fetcher=vi.fn<typeof fetch>(async()=>new Response(new ReadableStream<Uint8Array>({start(c){controller=c;}}),{headers:{'Content-Type':'text/event-stream'}}));
 vi.stubGlobal('fetch',fetcher);setCsrfToken('csrf');
 const onDelta=vi.fn();const signal=new AbortController().signal;
 const pending=streamWorkflowPairExplanation('r','p',body,onDelta,signal);
 await Promise.resolve();
 const bytes=new TextEncoder().encode(event('delta',{text:'Tên giống.'}));
 for(const byte of bytes) controller.enqueue(new Uint8Array([byte]));
 await vi.waitFor(()=>expect(onDelta).toHaveBeenCalledWith('Tên giống.'));
 controller.enqueue(new TextEncoder().encode(event('result',result)));controller.close();
 await expect(pending).resolves.toEqual(result);
 expect(fetcher.mock.calls[0][0]).toContain('/pairs/p/explanation/stream');
 const init=fetcher.mock.calls[0][1] as RequestInit;
 expect(init.credentials).toBe('include');expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('csrf');expect(init.signal).toBe(signal);
});
it.each([
 event('delta',{text:'unfinished'}),
 event('result',{status:'READY',explanation:null}),
 'event: delta\ndata: nope\n\n',
 event('delta',{text:12}),
 `event: ignored\ndata: ${'x'.repeat(140000)}\n\n`,
])('rejects incomplete, malformed or oversized streams',async text=>{
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(text,{headers:{'Content-Type':'text/event-stream'}})));
 await expect(streamWorkflowPairExplanation('r','p',body,()=>undefined)).rejects.toThrow();
});
it('ignores unknown events and accepts a cached result without deltas',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(event('future',{x:1})+event('result',result),{headers:{'Content-Type':'text/event-stream'}})));
 const delta=vi.fn();await expect(streamWorkflowPairExplanation('r','p',body,delta)).resolves.toEqual(result);expect(delta).not.toHaveBeenCalled();
});
