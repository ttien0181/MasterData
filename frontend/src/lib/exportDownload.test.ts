import { afterEach, expect, it, vi } from 'vitest';
import { downloadResolutionExport } from './api';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });
it('downloads an owned export through browser navigation so an R2 redirect needs no fetch CORS', async () => {
  const fetcher = vi.fn().mockRejectedValue(new TypeError('Cross-origin fetch unavailable'));
  vi.stubGlobal('fetch', fetcher);
  let destination = '';
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { destination = this.href; });
  await downloadResolutionExport('run', 'export');
  expect(destination).toMatch(/\/api\/v1\/mdm\/runs\/run\/exports\/export\/download$/);
  expect(fetcher).not.toHaveBeenCalled();
  expect(document.querySelector('a')).toBeNull();
});
