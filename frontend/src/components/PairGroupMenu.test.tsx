import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import PairGroupMenu from './PairGroupMenu';
import type {PairSummary} from '@/lib/workflowContracts';
afterEach(cleanup);
it('nests subgroups inside their Group and never exposes BLOCKED',()=>{
 const choose=vi.fn(),group=vi.fn();
 const summary={total:9,needs_review:2,groups:[{key:'A',total:9,subgroups:[{key:'A1_NAME_PHONE',total:5},{key:'A2_NAME_EMAIL',total:4}]},{key:'BLOCKED',total:3,subgroups:[]}]} as PairSummary;
 render(<PairGroupMenu summary={summary} filter={{}} onGroup={group} onSubgroup={choose} prefetchGroup={vi.fn()}/>);
 expect(screen.queryByText('Bị chặn')).not.toBeInTheDocument();
 expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Subgroup Group A'}));
 const menu=screen.getByRole('group',{name:'Subgroup của Group A'});
 fireEvent.click(within(menu).getByRole('button',{name:'Tên và email 4'}));
 expect(choose).toHaveBeenCalledWith('A','A2_NAME_EMAIL');
 expect(screen.queryByRole('group',{name:'Subgroup của Group A'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Subgroup Group B'}));
 expect(screen.getByText('Không có Subgroup')).toBeVisible();
 expect(group).not.toHaveBeenCalled();
});

it('keeps the required-review card stable while browsing Group C or accepted pairs',()=>{
 const summary:PairSummary={total:9,needs_review:0,accepted:4,denied:0,kept_separate:0,deferred:0,blocked:0,manual_available:5,auto_available:0,decision_revision:1,groups:[{key:'A',total:4,subgroups:[]},{key:'C',total:5,subgroups:[]}]};
 const select=vi.fn();
 const view=render(<PairGroupMenu summary={summary} filter={{group:'C'}} onGroup={select} onSubgroup={vi.fn()} prefetchGroup={vi.fn()}/>);
 const required=screen.getByRole('button',{name:/^Cần duyệt/});
 expect(within(required).getByText('0')).toBeInTheDocument();
 expect(required).toHaveAttribute('aria-pressed','false');fireEvent.click(required);expect(select).toHaveBeenCalledWith('');
 view.rerender(<PairGroupMenu summary={summary} filter={{resolution:'REQUIRED'}} onGroup={select} onSubgroup={vi.fn()} prefetchGroup={vi.fn()}/>);
 expect(required).toHaveAttribute('aria-pressed','true');
});

it('counts unresolved pairs in Group and Subgroup badges and reaches zero after review',()=>{
 const select=vi.fn();
 const summary:PairSummary={total:9,needs_review:2,accepted:3,denied:0,kept_separate:0,deferred:0,blocked:0,manual_available:2,auto_available:0,decision_revision:1,groups:[{key:'A',total:5,pending:2,unresolved:2,subgroups:[{key:'A1_NAME_PHONE',total:5,unresolved:2}]},{key:'C',total:4,pending:0,manual_available:0,unresolved:4,subgroups:[]}]};
 const props={filter:{group:'A'},onGroup:select,onSubgroup:vi.fn(),prefetchGroup:vi.fn()};
 const view=render(<PairGroupMenu {...props} summary={summary}/>);
 const a=screen.getByRole('button',{name:/^Group A/});
 expect(within(a).getByText('2')).toBeInTheDocument();
 expect(within(screen.getByRole('button',{name:/^Group C/})).getByText('4')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Subgroup Group A'}));
 expect(screen.getByRole('button',{name:'Tên và điện thoại 2'})).toBeVisible();
 const reviewed:PairSummary={...summary,needs_review:0,accepted:5,manual_available:0,decision_revision:2,groups:summary.groups.map(item=>item.key==='A'?{...item,pending:0,unresolved:0,subgroups:item.subgroups.map(subgroup=>({...subgroup,unresolved:0}))}:item)};
 view.rerender(<PairGroupMenu {...props} summary={reviewed}/>);
 expect(within(a).getByText('0')).toBeInTheDocument();
 expect(within(screen.getByRole('button',{name:/^Tất cả Group A/})).getByText('0')).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Tên và điện thoại 0'})).toBeVisible();
 fireEvent.click(a);expect(select).toHaveBeenCalledWith('A');
 view.rerender(<PairGroupMenu {...props} filter={{group:'A',resolution:'ACCEPTED'}} summary={reviewed}/>);
 expect(within(a).getByText('0')).toBeInTheDocument();
});
