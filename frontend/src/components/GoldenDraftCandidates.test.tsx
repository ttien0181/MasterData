import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {SessionCacheProvider} from './SessionCache';
import GoldenFieldEditor from './GoldenFieldEditor';
import type {GoldenRead} from '@/lib/workflowContracts';
afterEach(cleanup);
it('saving another field does not acknowledge an inherited pending choice',async()=>{
 const golden:GoldenRead={cluster_id:'cluster',cluster_revision:1,golden_revision:1,revision:0,status:'DRAFT',
  fields:{name:{value:'Inherited'},email:{value:'old@example.net'}},alternatives:{},
  selections:{name:{selection_mode:'MANUAL_OVERRIDE',value:'Inherited'}},requires_manual_review:['name']};
 const save=vi.fn().mockResolvedValue(true);
 render(<SessionCacheProvider userId="candidate-editor"><GoldenFieldEditor golden={golden} editable busy={false} onSave={save} runId="run"/></SessionCacheProvider>);
 fireEvent.change(screen.getByRole('textbox',{name:'Chỉnh tay email'}),{target:{value:'chosen@example.net'}});
 fireEvent.click(screen.getByRole('button',{name:'Lưu lựa chọn Golden'}));
 await waitFor(()=>expect(save).toHaveBeenCalledWith({email:{selection_mode:'MANUAL_OVERRIDE',value:'chosen@example.net'}},1,1));
});
it('shows an orphaned extra candidate and requires an explicit reason before discarding',async()=>{
 const golden:GoldenRead={cluster_id:'orphan',cluster_revision:2,golden_revision:3,revision:0,status:'DRAFT',
  fields:{},alternatives:{},selections:{},requires_manual_review:['obsolete_extra'],
  pending_selections:{obsolete_extra:[{selection_mode:'SOURCE_CHOICE',source_record_id:'removed',
   value:['old','extra'],ancestor_cluster_id:'old-group'}]}};
 const save=vi.fn().mockResolvedValue(true);
 render(<SessionCacheProvider userId="orphan-editor"><GoldenFieldEditor golden={golden} editable busy={false} onSave={save} runId="run"/></SessionCacheProvider>);
 expect(screen.getByText('["old","extra"]')).toBeInTheDocument();
 const discard=screen.getByRole('button',{name:'Bỏ lựa chọn cũ obsolete_extra'});
 expect(discard).toBeDisabled();
 fireEvent.change(screen.getByRole('textbox',{name:'Lý do bỏ lựa chọn obsolete_extra'}),{target:{value:'Nguồn đã bị loại'}});
 fireEvent.click(discard);
 expect(save).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'Lưu lựa chọn Golden'}));
 await waitFor(()=>expect(save).toHaveBeenCalledWith({obsolete_extra:{selection_mode:'DISCARD_PENDING',reason:'Nguồn đã bị loại'}},2,3));
});
