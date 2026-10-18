import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,expect,it} from 'vitest';
import {SessionCacheProvider} from './SessionCache';
import {usePairGroupView} from './usePairGroupView';
function View(){const v=usePairGroupView('r');return <><output>{JSON.stringify({filter:v.filter,offset:v.offset,search:v.search})}</output><button onClick={()=>v.change('group','')}>All</button><button onClick={()=>v.change('group','A_CONTACT')}>A</button><button onClick={()=>v.change('group','B_CONFLICT')}>B</button><button onClick={()=>v.setOffset(20)}>Page 3</button><button onClick={()=>v.setSearch('Customer')}>Search</button></>;}
const current=()=>JSON.parse(screen.getByRole('status').textContent!);
afterEach(cleanup);
it('starts with the MVP review taxonomy and keeps it when switching groups',()=>{
 render(<SessionCacheProvider userId="u"><View/></SessionCacheProvider>);
 expect(current().filter).toEqual({visibility:'review',taxonomy:'mvp_v1'});
 fireEvent.click(screen.getByText('A',{selector:'button'}));
 expect(current().filter).toEqual({group:'A_CONTACT',visibility:'review',taxonomy:'mvp_v1'});
});
it('resets to the first unsearched all-pairs page after group navigation',()=>{
 render(<SessionCacheProvider userId="u"><View/></SessionCacheProvider>);
 fireEvent.click(screen.getByText('A',{selector:'button'}));fireEvent.click(screen.getByText('Page 3'));fireEvent.click(screen.getByText('Search'));fireEvent.click(screen.getByText('All'));
 expect(current()).toEqual({filter:{visibility:'review',taxonomy:'mvp_v1'},offset:0,search:''});
});
it('restores the selected MVP group page and search after another group',()=>{
 render(<SessionCacheProvider userId="u"><View/></SessionCacheProvider>);
 fireEvent.click(screen.getByText('A',{selector:'button'}));fireEvent.click(screen.getByText('Page 3'));fireEvent.click(screen.getByText('Search'));fireEvent.click(screen.getByText('B',{selector:'button'}));
 expect(current()).toEqual({filter:{group:'B_CONFLICT',visibility:'review',taxonomy:'mvp_v1'},offset:0,search:''});
 fireEvent.click(screen.getByText('A',{selector:'button'}));
 expect(current()).toEqual({filter:{group:'A_CONTACT',visibility:'review',taxonomy:'mvp_v1'},offset:20,search:'Customer'});
});
