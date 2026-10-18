import {useEffect} from 'react';
import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import DemoExperience from './DemoExperience';
import MasterBotProvider from './MasterBotProvider';

const navigation=vi.hoisted(()=>({pathname:'/app/sessions/demo/data',demo:true}));
vi.mock('next/navigation',()=>({usePathname:()=>navigation.pathname}));
vi.mock('./AppUserContext',()=>({useAppUser:()=>({id:'judge-tour',demo:navigation.demo?{run_id:'demo'}:null})}));
vi.mock('./MasterBotText',()=>({
 default:({text,onComplete}:{text:string;onComplete?:()=>void})=>{useEffect(()=>{onComplete?.();},[text]);return <p>{text}</p>;},
 HighlightedText:({text}:{text:string})=><>{text}</>,
}));
beforeEach(()=>{sessionStorage.clear();localStorage.clear();navigation.pathname='/app/sessions/demo/data';navigation.demo=true;});
afterEach(cleanup);
function host(children=<div data-testid="empty-space">Khoảng trống</div>){return <MasterBotProvider canExplain userId="judge-tour"><DemoExperience onEnd={vi.fn()}>{children}</DemoExperience></MasterBotProvider>;}
const bubble=()=>screen.getByRole('region',{name:'Trò chuyện với Master Bot'});

it('offers Next inside the bot even while the highlighted target is unavailable',async()=>{
 render(host());
 const next=await screen.findByRole('button',{name:'Tiếp theo'});
 expect(bubble()).toContainElement(next);
 expect(bubble()).toHaveTextContent('Mình đang kiểm tra dữ liệu');
 fireEvent.click(next);
 await waitFor(()=>expect(bubble()).toHaveTextContent('Khi đối chiếu hoàn tất'));
});

it('advances on empty space without dismissing the guide, including the final hint',async()=>{
 render(host());await screen.findByRole('button',{name:'Tiếp theo'});
 fireEvent.click(screen.getByTestId('empty-space'));
 await waitFor(()=>expect(bubble()).toHaveTextContent('Khi đối chiếu hoàn tất'));
 fireEvent.click(screen.getByTestId('empty-space'));
 await waitFor(()=>expect(bubble()).toHaveTextContent('Bạn đã xem các gợi ý'));
 expect(screen.getByRole('button',{name:'Bỏ qua hướng dẫn'})).toBeVisible();
});

it('skips explicitly and can restart from the existing demo controls',async()=>{
 render(host());fireEvent.click(await screen.findByRole('button',{name:'Bỏ qua hướng dẫn'}));
 expect(screen.queryByRole('button',{name:'Tiếp theo'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByTestId('empty-space'));
 expect(screen.queryByRole('button',{name:'Tiếp theo'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Hướng dẫn demo'}));
 expect(await screen.findByRole('button',{name:'Tiếp theo'})).toBeVisible();
});

it('keeps normal business buttons working without advancing unrelated hints',async()=>{
 const action=vi.fn();render(host(<button onClick={action}>Nghiệp vụ khác</button>));
 await screen.findByRole('button',{name:'Tiếp theo'});
 fireEvent.click(screen.getByRole('button',{name:'Nghiệp vụ khác'}));
 expect(action).toHaveBeenCalledOnce();expect(bubble()).toHaveTextContent('Mình đang kiểm tra dữ liệu');
});

it('restarts a skipped demo guide from the shared header action',async()=>{
 render(host());fireEvent.click(await screen.findByRole('button',{name:'Bỏ qua hướng dẫn'}));
 act(()=>window.dispatchEvent(new Event('masterone:start-guide')));
 expect(await screen.findByRole('button',{name:'Tiếp theo'})).toBeVisible();
 expect(bubble()).toHaveTextContent('Mình đang kiểm tra dữ liệu');
});

it('runs the guide for a normal account without entering demo or changing data',async()=>{
 navigation.demo=false;navigation.pathname='/app';render(host());
 expect(screen.queryByRole('button',{name:'Tiếp theo'})).not.toBeInTheDocument();
 act(()=>window.dispatchEvent(new Event('masterone:start-guide')));
 expect(await screen.findByRole('button',{name:'Tiếp theo'})).toBeVisible();
 expect(bubble()).toHaveTextContent('Tổng quan');
 expect(screen.queryByText('Phiên demo')).not.toBeInTheDocument();
});

it('advances a dialog guide on its blank backdrop without closing the dialog',async()=>{
 navigation.pathname='/app/sessions/demo/decisions';const close=vi.fn();
 render(host(<dialog open aria-label="Đối chiếu cặp hồ sơ" onMouseDown={close}><div data-demo="pair-comparison">Bảng hồ sơ</div></dialog>));
 await waitFor(()=>expect(bubble()).toHaveTextContent('Đây là thông tin thực tế'));
 const dialog=screen.getByRole('dialog',{name:'Đối chiếu cặp hồ sơ'});
 fireEvent.mouseDown(dialog);fireEvent.click(dialog);
 expect(close).not.toHaveBeenCalled();
 await waitFor(()=>expect(bubble()).toHaveTextContent('Khi đã xem xong'));
 expect(within(dialog).getByRole('button',{name:'Tiếp theo'})).toBeVisible();
});
