import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,expect,it} from 'vitest';
import {SessionCacheProvider} from './SessionCache';
import {usePairGroupView} from './usePairGroupView';
function View(){const v=usePairGroupView('r');return <><output>{v.filter.resolution || 'ALL'}</output><button onClick={()=>v.change('resolution','')}>All</button><button onClick={()=>v.change('group','A')}>A</button><button onClick={()=>v.change('group','')}>Back</button></>;}
afterEach(cleanup);
it('starts with required work and keeps that resolution when switching groups',()=>{
 render(<SessionCacheProvider userId="u"><View/></SessionCacheProvider>);expect(screen.getByRole('status')).toHaveTextContent('REQUIRED');
 fireEvent.click(screen.getByText('A',{selector:'button'}));expect(screen.getByRole('status')).toHaveTextContent('REQUIRED');
});
it('restores an explicit all-pairs view after group navigation',()=>{
 render(<SessionCacheProvider userId="u"><View/></SessionCacheProvider>);fireEvent.click(screen.getByText('All'));fireEvent.click(screen.getByText('A',{selector:'button'}));fireEvent.click(screen.getByText('Back'));expect(screen.getByRole('status')).toHaveTextContent('ALL');
});
