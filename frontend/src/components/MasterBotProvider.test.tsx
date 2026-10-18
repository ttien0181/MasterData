import {act,cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import MasterBotProvider,{useMasterBot} from './MasterBotProvider';
const navigation=vi.hoisted(()=>({pathname:'/app/sessions/r/data'}));
vi.mock('next/navigation',()=>({usePathname:()=>navigation.pathname}));
function Controls(){const bot=useMasterBot()!;return <><button onClick={bot.toggleIntro}>Toggle intro</button><button onClick={()=>{bot.rememberReply('Giải thích gần nhất: điện thoại trùng khớp.');bot.show();}}>Explain</button><button onClick={()=>bot.explainMetric("Tỷ lệ hồ sơ cần kiểm tra: 25%. Có cảnh báo chưa có nghĩa là hồ sơ trùng.")}>Metric</button><button onClick={()=>{bot.show();bot.setHasReply(true);}}>Pending reply</button><button onClick={()=>bot.setReplyReady?.(true)}>Reply ready</button><button onClick={()=>bot.setDemoGuide?.({id:'guide-test',text:'Hướng dẫn đối chiếu hồ sơ.',progress:'1 / 2',next:()=>{},skip:()=>bot.setDemoGuide?.(null)})}>Demo guide</button><span data-testid="enabled">{String(bot.introEnabled)}</span></>;}
function host(){return <MasterBotProvider canExplain userId="test"><Controls/></MasterBotProvider>;}
const bubble=()=>document.querySelector('section[aria-label="Trò chuyện với Master Bot"]')!;
beforeEach(()=>{vi.useFakeTimers();localStorage.clear();navigation.pathname='/app/sessions/r/data';});
afterEach(()=>{cleanup();document.querySelectorAll('dialog').forEach(dialog=>dialog.remove());vi.useRealTimers();});
it('introduces every page visit, waits for all text then hides after five seconds, and replays',()=>{
 const view=render(host());expect(bubble()).toBeVisible();
 act(()=>vi.advanceTimersByTime(500));expect(bubble().textContent).not.toContain('chuyển sang đối chiếu');
 act(()=>vi.advanceTimersByTime(10000));expect(bubble()).toHaveTextContent('Bước 1');expect(bubble()).toBeVisible();
 act(()=>vi.advanceTimersByTime(4999));expect(bubble()).toBeVisible();
 act(()=>vi.advanceTimersByTime(1));expect(bubble()).not.toBeVisible();
 fireEvent.click(screen.getByRole('button',{name:'Mở trò chuyện Master Bot'}));act(()=>vi.advanceTimersByTime(10000));expect(bubble()).toHaveTextContent('Bước 1');
 navigation.pathname='/app/sessions/r/clusters';view.rerender(host());act(()=>vi.advanceTimersByTime(10000));expect(bubble()).toHaveTextContent('Bước 3');
 navigation.pathname='/app/sessions/r/data';view.rerender(host());act(()=>vi.advanceTimersByTime(10000));expect(bubble()).toHaveTextContent('Bước 1');
});
it('persists disabling introductions while preserving explicit answers and the 30 second timeout',()=>{
 const view=render(host());fireEvent.click(screen.getByText('Toggle intro'));expect(bubble()).not.toBeVisible();
 navigation.pathname='/app/sessions/r/clusters';view.rerender(host());expect(bubble()).not.toBeVisible();
 fireEvent.click(screen.getByText('Explain'));act(()=>vi.advanceTimersByTime(10000));expect(bubble()).toHaveTextContent('Giải thích gần nhất');
 // A standalone replay has finished revealing by this point.
 act(()=>vi.advanceTimersByTime(29999));expect(bubble()).toBeVisible();
 act(()=>vi.advanceTimersByTime(1));expect(bubble()).not.toBeVisible();
 fireEvent.click(screen.getByRole('button',{name:'Mở trò chuyện Master Bot'}));act(()=>vi.advanceTimersByTime(10000));expect(bubble()).toHaveTextContent('Giải thích gần nhất');
 view.unmount();render(host());expect(screen.getByTestId('enabled')).toHaveTextContent('false');expect(bubble()).not.toBeVisible();
});
it('manual closing cancels pending dismissal and disabling guides never disables explain',()=>{
 render(host());fireEvent.click(screen.getByRole('button',{name:'Thu gọn Master Bot'}));
 act(()=>vi.advanceTimersByTime(10000));expect(bubble()).not.toBeVisible();
 fireEvent.click(screen.getByText('Explain'));act(()=>vi.advanceTimersByTime(2000));expect(bubble()).toBeVisible();
 fireEvent.click(screen.getByRole('button',{name:'Thu gọn Master Bot'}));act(()=>vi.advanceTimersByTime(30000));expect(bubble()).not.toBeVisible();
});

it('introduces only the first child per step visit and never repeats the parent on return',async()=>{
 navigation.pathname='/app/sessions/r/decisions';const view=render(host());
 fireEvent.click(screen.getByRole('button',{name:'Thu gọn Master Bot'}));
 const dialog=document.createElement('dialog');dialog.setAttribute('aria-label','Đối chiếu cặp hồ sơ');
 await act(async()=>{document.body.append(dialog);dialog.setAttribute('open','');});
 act(()=>vi.advanceTimersByTime(10000));expect(bubble()).toHaveTextContent('đặt hai hồ sơ cạnh nhau');
 fireEvent.click(screen.getByRole('button',{name:'Thu gọn Master Bot'}));
 await act(async()=>dialog.removeAttribute('open'));expect(bubble()).not.toBeVisible();
 await act(async()=>dialog.setAttribute('open',''));expect(bubble()).not.toBeVisible();
 await act(async()=>dialog.setAttribute('aria-label','Đối chiếu nhóm khác'));expect(bubble()).not.toBeVisible();
 await act(async()=>dialog.remove());
 navigation.pathname='/app/sessions/r/clusters';view.rerender(host());act(()=>vi.advanceTimersByTime(10000));expect(bubble()).toHaveTextContent('Bước 3');
 navigation.pathname='/app/sessions/r/decisions';view.rerender(host());act(()=>vi.advanceTimersByTime(10000));expect(bubble()).toHaveTextContent('Bước 2');
 await act(async()=>{dialog.setAttribute('aria-label','Đối chiếu cặp hồ sơ');dialog.setAttribute('open','');document.body.append(dialog);});
 act(()=>vi.advanceTimersByTime(10000));expect(bubble()).toHaveTextContent('đặt hai hồ sơ cạnh nhau');
 await act(async()=>dialog.remove());
});
it('does not replace an explicit answer with repeated child introductions or route aliases',async()=>{
 navigation.pathname='/app/sessions/r/decisions';const view=render(host());
 fireEvent.click(screen.getByText('Explain'));act(()=>vi.advanceTimersByTime(10000));
 const dialog=document.createElement('dialog');dialog.setAttribute('aria-label','Đối chiếu cặp hồ sơ');dialog.setAttribute('open','');
 await act(async()=>document.body.append(dialog));act(()=>vi.advanceTimersByTime(1000));expect(bubble()).toHaveTextContent('Giải thích gần nhất');
 await act(async()=>dialog.removeAttribute('open'));act(()=>vi.advanceTimersByTime(1000));expect(bubble()).toHaveTextContent('Giải thích gần nhất');
 fireEvent.click(screen.getByRole('button',{name:'Thu gọn Master Bot'}));
 navigation.pathname='/app/sessions/r/decisions/pairs';view.rerender(host());expect(bubble()).not.toBeVisible();
 await act(async()=>dialog.setAttribute('open',''));expect(bubble()).not.toBeVisible();
 await act(async()=>dialog.remove());
});

it('explains metrics with guides disabled, prioritizes them over a pair reply and replays the latest metric',()=>{
 render(host());fireEvent.click(screen.getByText('Toggle intro'));
 fireEvent.click(screen.getByText('Explain'));act(()=>vi.advanceTimersByTime(10000));
 fireEvent.click(screen.getByText('Metric'));act(()=>vi.advanceTimersByTime(10000));
 expect(bubble()).toHaveTextContent('Tỷ lệ hồ sơ cần kiểm tra: 25%');expect(bubble()).not.toHaveTextContent('Giải thích gần nhất');
 fireEvent.click(screen.getByRole('button',{name:'Thu gọn Master Bot'}));
 fireEvent.click(screen.getByRole('button',{name:'Mở trò chuyện Master Bot'}));act(()=>vi.advanceTimersByTime(10000));
 expect(bubble()).toHaveTextContent('Tỷ lệ hồ sơ cần kiểm tra: 25%');
 fireEvent.click(screen.getByText('Explain'));act(()=>vi.advanceTimersByTime(10000));expect(bubble()).toHaveTextContent('Giải thích gần nhất');
});

it('dismisses a metric five seconds after typing finishes and restarts the countdown for each new metric',()=>{
 render(host());fireEvent.click(screen.getByText('Toggle intro'));fireEvent.click(screen.getByText('Metric'));
 act(()=>vi.advanceTimersByTime(500));expect(bubble()).toBeVisible();
 act(()=>vi.advanceTimersByTime(5000));expect(bubble()).toHaveTextContent('Có cảnh báo chưa có nghĩa là hồ sơ trùng.');expect(bubble()).toBeVisible();
 act(()=>vi.advanceTimersByTime(4999));expect(bubble()).toBeVisible();
 act(()=>vi.advanceTimersByTime(1));expect(bubble()).not.toBeVisible();
 fireEvent.click(screen.getByRole('button',{name:'Mở trò chuyện Master Bot'}));act(()=>vi.advanceTimersByTime(10000));expect(bubble()).toBeVisible();
 act(()=>vi.advanceTimersByTime(5000));expect(bubble()).not.toBeVisible();
});

it('keeps a slow explanation open until the reply arrives, then dismisses after 30 seconds',()=>{
 render(host());fireEvent.click(screen.getByText('Pending reply'));
 act(()=>vi.advanceTimersByTime(45000));expect(bubble()).toBeVisible();
 fireEvent.click(screen.getByText('Reply ready'));act(()=>vi.advanceTimersByTime(29999));expect(bubble()).toBeVisible();
 act(()=>vi.advanceTimersByTime(1));expect(bubble()).not.toBeVisible();
});

it('keeps the demo guide open after typing until Next or Skip, then remembers its last words',()=>{
 render(host());fireEvent.click(screen.getByText('Demo guide'));
 act(()=>vi.advanceTimersByTime(10000));expect(bubble()).toHaveTextContent('Hướng dẫn đối chiếu hồ sơ.');
 expect(screen.getByRole('button',{name:'Tiếp theo'})).toBeVisible();
 act(()=>vi.advanceTimersByTime(60000));expect(bubble()).toBeVisible();
 fireEvent.click(screen.getByRole('button',{name:'Bỏ qua hướng dẫn'}));expect(bubble()).not.toBeVisible();
 fireEvent.click(screen.getByRole('button',{name:'Mở trò chuyện Master Bot'}));act(()=>vi.advanceTimersByTime(10000));
 expect(bubble()).toHaveTextContent('Hướng dẫn đối chiếu hồ sơ.');
});
