import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SessionCacheProvider, useStoredState } from './SessionCache';
import { useTabView } from './useTabView';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('retries a browser-clamped vertical position after delayed content arrives', async () => {
  let height = 100, y = 0;
  vi.spyOn(window, 'scrollY', 'get').mockImplementation(() => y);
  vi.spyOn(window, 'scrollTo').mockImplementation((options: ScrollToOptions | number) => { y = Math.min(height, typeof options === 'number' ? options : options.top || 0); });
  function SavedView() { useStoredState('r', 'view:golden', { y: 900, horizontal: {}, details: {} }); return null; }
  function Tab({ late }: { late: boolean }) {
    const view = useTabView('r', 'golden', true);
    return <section ref={view}>Main records{late && <section>Delayed proposal content</section>}</section>;
  }
  const host = (late: boolean) => <SessionCacheProvider userId="u"><SavedView /><Tab late={late} /></SessionCacheProvider>;
  const view = render(host(false));
  await waitFor(() => expect(y).toBe(100));
  height = 1200;
  view.rerender(host(true));
  await waitFor(() => expect(y).toBe(900));
});

it('restores independent Group scroll and details when the same table element is reused', async () => {
  let y = 0;
  vi.spyOn(window, 'scrollY', 'get').mockImplementation(() => y);
  vi.spyOn(window, 'scrollTo').mockImplementation((options: ScrollToOptions | number) => { y = typeof options === 'number' ? options : options.top || 0; });
  function Tab({ group }: { group: string }) {
    const view = useTabView('r', `decisions:${group}`, true);
    return <section ref={view}><div data-scroll-key="pairs"><details data-view-key={`pair:${group}`}><summary>Evidence</summary>{group}</details></div></section>;
  }
  const host = (group: string) => <SessionCacheProvider userId="u"><Tab group={group} /></SessionCacheProvider>;
  const view = render(host('A'));
  await waitFor(() => expect(y).toBe(0));
  fireEvent.wheel(window); y = 350; fireEvent.scroll(window);
  const table = document.querySelector('[data-scroll-key="pairs"]') as HTMLElement;
  table.scrollLeft = 120; fireEvent.scroll(table);
  const evidence = document.querySelector('details')!;
  evidence.open = true; fireEvent(evidence, new Event('toggle'));
  view.rerender(host('D'));
  await waitFor(() => expect(y).toBe(0));
  expect(table.scrollLeft).toBe(0);
  expect(evidence.open).toBe(false);
  fireEvent.wheel(window); y = 700; fireEvent.scroll(window);
  table.scrollLeft = 240; fireEvent.scroll(table);
  view.rerender(host('A'));
  await waitFor(() => expect(y).toBe(350));
  expect(table.scrollLeft).toBe(120);
  expect(evidence.open).toBe(true);
});
