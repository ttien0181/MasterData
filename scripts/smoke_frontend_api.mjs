// Execute unchanged frontend api.ts against a disposable backend fixture server.
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import assert from 'node:assert/strict';

const toURL = (source) => 'data:text/javascript;base64,' + Buffer.from(source).toString('base64');
const choices = toURL(stripTypeScriptTypes(readFileSync('frontend/src/lib/resolutionChoices.ts', 'utf8'), { mode: 'transform' }));
const source = stripTypeScriptTypes(readFileSync('frontend/src/lib/api.ts', 'utf8'), { mode: 'transform' });
const api = await import(toURL(source.replaceAll('"./resolutionChoices"', JSON.stringify(choices))));
api.setCsrfToken(process.env.SMOKE_CSRF);
const run = process.env.SMOKE_RUN;
assert.equal((await api.getBackendHealth()).status, 'ok');
const overview = await api.getResolutionOverview(run);
assert.equal(overview.status, 'COMPLETED');
const page = await api.getResolutionItems(run, 'review_pairs', { limit: 1, offset: 0 });
assert.equal(page.total, 1);
const detail = await api.getResolutionItem(run, 'review_pairs', page.items[0].id);
assert.equal(detail.records.length, 2);
assert.ok(Array.isArray(detail.pairs));
await api.decideReviewPair(run, page.items[0].id, 'no_merge', page.items[0].revision);
assert.equal((await api.getResolutionItems(run, 'review_pairs')).total, 0);
assert.ok((await api.getResolutionAudit(run, { limit: 1 })).total >= 1);
console.log('PASS: actual frontend api.ts GET/detail/decision/audit against isolated backend');
