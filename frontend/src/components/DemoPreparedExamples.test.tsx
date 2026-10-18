import {cleanup,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import DemoPreparedExamples from './DemoPreparedExamples';
const mocks=vi.hoisted(()=>({prepare:vi.fn(),invalidate:vi.fn().mockResolvedValue(undefined)}));
vi.mock('@/lib/api',()=>({prepareDemoExamples:mocks.prepare}));
vi.mock('./SessionCache',()=>({sessionKey:(...parts:unknown[])=>['session',...parts],useSessionCache:()=>({userId:'judge',client:{invalidateQueries:mocks.invalidate}})}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
it('labels prepared examples and refreshes real session queries after backfill',async()=>{
 mocks.prepare.mockResolvedValue({status:'READY',sample_groups:8});
 render(<DemoPreparedExamples runId="demo"/>);
 expect(await screen.findByText('8 nhóm mẫu đã được chuẩn bị sẵn.')).toBeVisible();
 expect(screen.getByText(/không phải quyết định của bạn/)).toBeVisible();
 await waitFor(()=>expect(mocks.invalidate).toHaveBeenCalledWith({queryKey:['session','judge','demo']}));
});
it('shows recoverable preparation errors without inventing result rows',async()=>{
 mocks.prepare.mockRejectedValue(new Error('Mất kết nối.'));
 render(<DemoPreparedExamples runId="demo"/>);
 expect(await screen.findByText('Mất kết nối.')).toBeVisible();
 expect(screen.getByRole('button',{name:'Thử lại'})).toBeVisible();
 expect(mocks.invalidate).not.toHaveBeenCalled();
});
