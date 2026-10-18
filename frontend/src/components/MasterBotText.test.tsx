import {act,cleanup,render,screen} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {masterBotGuide} from '@/lib/masterBotGuide';
import MasterBotText,{HighlightedText} from './MasterBotText';
afterEach(()=>{cleanup();vi.useRealTimers();});
it('reveals gradually, continues incoming text and stops on unmount',()=>{
 vi.useFakeTimers();const view=render(<MasterBotText text="Điện thoại trùng khớp"/>);
 expect(view.container.textContent).toBe('');
 act(()=>vi.advanceTimersByTime(64));expect(view.container.textContent?.length).toBeGreaterThan(0);expect(view.container.textContent).not.toBe('Điện thoại trùng khớp');
 act(()=>vi.advanceTimersByTime(1000));expect(view.container.textContent).toBe('Điện thoại trùng khớp');
 view.rerender(<MasterBotText text="Điện thoại trùng khớp. Cần kiểm tra"/>);
 expect(view.container.textContent).toBe('Điện thoại trùng khớp');
 act(()=>vi.advanceTimersByTime(32));expect(view.container.textContent).toBe('Điện thoại trùng khớp. ');
 view.unmount();expect(vi.getTimerCount()).toBe(0);
});
it('highlights fields, meaningful signals and contact data without interpreting HTML',()=>{
 const view=render(<HighlightedText text="Điện thoại 0386967972 trùng khớp, email a@b.vn chưa xác minh. <script>x</script>"/>);
 expect(screen.getByText('Điện thoại').tagName).toBe('MARK');
 expect(screen.getByText('0386967972').tagName).toBe('MARK');
 expect(screen.getByText('a@b.vn').tagName).toBe('MARK');
 expect(view.container.querySelector('script')).toBeNull();
 expect(view.container.textContent).toContain('<script>x</script>');
});

it('highlights key business actions in every page introduction while preserving the wording',()=>{
 for(const path of ['/data','/decisions','/clusters','/golden','/utilities']){
  const text=masterBotGuide(`/app/sessions/r${path}`);
  const view=render(<HighlightedText text={text}/>);
  expect(view.container.querySelector('mark')).not.toBeNull();
  expect(view.container.textContent).toBe(text);view.unmount();
 }
});
