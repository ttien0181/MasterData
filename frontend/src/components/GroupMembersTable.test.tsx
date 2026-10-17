import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import {afterEach,expect,it} from 'vitest';
import GroupMembersTable from './GroupMembersTable';

afterEach(cleanup);
it('keeps missing values visible as differences and filters out equal fields',()=>{
 const members=[
  {record_id:'a',source_system:'CRM',name:'An',fields:{name:'An',phone:'090',email:'a@test.vn'}},
  {record_id:'b',source_system:'ERP',name:'An',fields:{name:'An',phone:'091',email:''}},
 ];
 render(<GroupMembersTable members={members}/>);
 fireEvent.click(screen.getByRole('checkbox',{name:'Chỉ hiện trường khác nhau'}));
 const table=screen.getByRole('table',{name:'Đối chiếu thành viên nhóm'});
 expect(within(table).queryByRole('rowheader',{name:'Họ tên'})).not.toBeInTheDocument();
 expect(within(table).getByRole('rowheader',{name:/Điện thoại/})).toBeVisible();
 const row=within(table).getByRole('rowheader',{name:/Email/}).closest('tr')!;
 expect(within(row).getByText('Thiếu dữ liệu')).toBeVisible();
 expect(within(row).getByText('a@test.vn')).toBeVisible();
});
