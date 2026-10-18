import {cleanup,fireEvent,render,screen,within,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import GoldenFieldEditor from './GoldenFieldEditor';
import {SessionCacheProvider} from './SessionCache';
import type {GoldenRead} from '@/lib/workflowContracts';

afterEach(cleanup);
const members=[
 {record_id:'a',external_id:'A',source_system:'CSV',name:'PHAN MINH KHOA',fields:{name:'PHAN MINH KHOA',phone:'0386967972'}},
 {record_id:'b',external_id:'B',source_system:'CRM',name:'Phan Minh Khoa',fields:{name:'Phan Minh Khoa',phone:'+84386967972'}},
];
const golden:GoldenRead={cluster_id:'c',cluster_revision:1,golden_revision:2,revision:1,status:'DRAFT',
 fields:{name:{value:'PHAN MINH KHOA',source_record_id:'a',source_system:'CSV'},phone:{value:'0386967972',source_record_id:'a',source_system:'CSV'}},
 alternatives:{name:members.map(m=>({source_record_id:m.record_id,source_system:m.source_system,value:m.fields.name,auto_rule_available:true})),
 phone:members.map(m=>({source_record_id:m.record_id,source_system:m.source_system,value:'0386967972',auto_rule_available:true}))},selections:{}};

it('selects a source cell in the comparison table and saves source identity with fenced revisions',async()=>{
 const save=vi.fn().mockResolvedValue({}),dirty=vi.fn();
 render(<GoldenFieldEditor customerView members={members} golden={golden} editable busy={false} onSave={save} onDirtyChange={dirty}/>);
 const table=screen.getByRole('table',{name:'Đối chiếu thành viên nhóm'});
 const row=within(table).getByRole('rowheader',{name:/Họ tên/}).closest('tr')!;
 const cells=within(row).getAllByRole('button',{name:/^Giữ Họ tên từ/});
 expect(cells[0]).toHaveAttribute('aria-pressed','true');
 fireEvent.click(cells[1]);
 expect(cells[1]).toHaveAttribute('aria-pressed','true');expect(cells[0]).toHaveAttribute('aria-pressed','false');
 expect(within(row).getByRole('cell',{name:/Thông tin giữ lại/})).toHaveTextContent('Phan Minh Khoa');
 fireEvent.click(screen.getByRole('button',{name:'Lưu thông tin đại diện'}));
 await waitFor(()=>expect(save).toHaveBeenCalledWith({name:{selection_mode:'SOURCE_CHOICE',source_record_id:'b'}},1,2));
 expect(screen.queryByRole('heading',{name:'Trường hồ sơ đại diện'})).not.toBeInTheDocument();
});

it('preserves a source draft on return and prevents saving it against a newer server revision',async()=>{
 const save=vi.fn(),dirty=vi.fn();
 const host=(data:GoldenRead,open=true)=><SessionCacheProvider userId="source-draft">{open&&<GoldenFieldEditor customerView runId="r" members={members} golden={data} editable busy={false} onSave={save} onDirtyChange={dirty}/>}</SessionCacheProvider>;
 const view=render(host(golden));
 fireEvent.click(screen.getByRole('button',{name:/^Giữ Họ tên từ CRM/}));
 view.rerender(host(golden,false));view.rerender(host({...golden,golden_revision:3}));
 expect(screen.getByRole('button',{name:/^Giữ Họ tên từ CRM/})).toHaveAttribute('aria-pressed','true');
 expect(screen.getByRole('alert')).toHaveTextContent('Dữ liệu đã thay đổi');
 expect(screen.getByRole('button',{name:'Lưu thông tin đại diện'})).toBeDisabled();
 expect(screen.getByRole('button',{name:/^Giữ Họ tên từ CSV/})).toBeDisabled();
 expect(save).not.toHaveBeenCalled();
});

it('keeps readonly and missing source values unselectable',()=>{
 render(<GoldenFieldEditor customerView members={members} golden={golden} editable={false} busy={false} onSave={vi.fn()}/>);
 const cells=screen.getAllByRole('button',{name:/^Giữ /});
 expect(cells.length).toBeGreaterThan(0);for(const cell of cells)expect(cell).toBeDisabled();
 expect(screen.queryByRole('button',{name:/^Giữ Email/})).not.toBeInTheDocument();
});

it('does not offer empty source values as hồ sơ đại diện choices',()=>{
 const data={...golden,alternatives:{...golden.alternatives,email:[{source_record_id:'a',source_system:'CSV',value:'',auto_rule_available:true}]}};
 render(<GoldenFieldEditor customerView members={members} golden={data} editable busy={false} onSave={vi.fn()}/>);
 expect(screen.queryByRole('button',{name:/^Giữ Email/})).not.toBeInTheDocument();
});

it('shows the normalized kept contact while retaining the exact selected source',()=>{
 render(<GoldenFieldEditor customerView members={members} golden={golden} editable busy={false} onSave={vi.fn()}/>);
 fireEvent.click(screen.getByRole('button',{name:/^Giữ Điện thoại từ CRM/}));
 expect(screen.getByRole('cell',{name:'Thông tin giữ lại Điện thoại'})).toHaveTextContent('0386967972');
 expect(screen.getByRole('button',{name:/^Giữ Điện thoại từ CRM/})).toHaveAttribute('aria-pressed','true');
 expect(screen.getByRole('button',{name:/^Giữ Điện thoại từ CSV/})).toHaveAttribute('aria-pressed','false');
});

it('keeps the chosen source and its value while the member page changes',()=>{
 const save=vi.fn();
 const host=(page:typeof members)=><GoldenFieldEditor customerView members={page} totalMemberCount={2} golden={golden} editable busy={false} onSave={save}/>;
 const view=render(host([members[0]]));
 fireEvent.click(screen.getByRole('button',{name:/^Giữ Họ tên từ CSV/}));
 view.rerender(host([members[1]]));
 expect(screen.getByRole('button',{name:/^Giữ Họ tên từ CRM/})).toHaveAttribute('aria-pressed','false');
 expect(screen.getByRole('cell',{name:'Thông tin giữ lại Họ tên'})).toHaveTextContent('PHAN MINH KHOA');
 expect(screen.getByRole('cell',{name:'Thông tin giữ lại Họ tên'})).toHaveTextContent('Hồ sơ ở trang khác');
 expect(screen.queryByRole('navigation',{name:'Trang thành viên'})).not.toBeInTheDocument();
});

it('supports inline manual values in the same row and clears source highlighting',async()=>{
 const save=vi.fn().mockResolvedValue({});
 render(<GoldenFieldEditor customerView members={members} golden={golden} editable busy={false} onSave={save}/>);
 fireEvent.click(screen.getByLabelText('Nhập giá trị khác Họ tên'));
 fireEvent.change(screen.getByRole('textbox',{name:'Nhập trực tiếp name'}),{target:{value:'Phan Minh Khoa đã kiểm tra'}});
 expect(screen.getByRole('button',{name:/^Giữ Họ tên từ CSV/})).toHaveAttribute('aria-pressed','false');
 expect(screen.getByRole('button',{name:/^Giữ Họ tên từ CRM/})).toHaveAttribute('aria-pressed','false');
 fireEvent.click(screen.getByRole('button',{name:'Lưu thông tin đại diện'}));
 await waitFor(()=>expect(save).toHaveBeenCalledWith({name:{selection_mode:'MANUAL_OVERRIDE',value:'Phan Minh Khoa đã kiểm tra'}},1,2));
});
