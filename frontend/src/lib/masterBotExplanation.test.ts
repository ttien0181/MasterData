import {expect,it} from 'vitest';
import {masterBotReminder} from './masterBotExplanation';
it('builds a friendly reminder from the actual fields without adding unrelated ones',()=>{
 expect(masterBotReminder(['name','phone','email','address'])).toBe('Bạn nhớ kiểm tra lại họ tên, điện thoại, email và địa chỉ trước khi quyết định gộp nhé.');
 expect(masterBotReminder(['dob','dob'])).toBe('Bạn nhớ kiểm tra lại ngày sinh trước khi quyết định gộp nhé.');
 expect(masterBotReminder([])).toBe('');expect(masterBotReminder()).toBe('');
});
