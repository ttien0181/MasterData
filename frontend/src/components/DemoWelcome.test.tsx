import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import DemoWelcome from './DemoWelcome';

const api=vi.hoisted(()=>({startProductDemo:vi.fn()}));
vi.mock('@/lib/api',()=>api);
vi.mock('./AppSession',()=>({useAppUser:()=>({demo:{run_id:null}})}));
vi.mock('./SampleLibrary',()=>({SampleFiles:()=>null}));
afterEach(cleanup);
beforeEach(()=>{api.startProductDemo.mockReset();api.startProductDemo.mockRejectedValue(new Error('test stopped before navigation'));});

it('starts the judge demo without embedding by default',async()=>{
 render(<DemoWelcome/>);
 expect(screen.getByRole('checkbox',{name:/So sánh tên và địa chỉ bằng AI/})).not.toBeChecked();
 fireEvent.click(screen.getByRole('button',{name:/Bắt đầu trải nghiệm/}));
 await waitFor(()=>expect(api.startProductDemo).toHaveBeenCalledWith(false));
});

it('lets a judge explicitly enable embedding before starting',async()=>{
 render(<DemoWelcome/>);
 fireEvent.click(screen.getByRole('checkbox',{name:/So sánh tên và địa chỉ bằng AI/}));
 fireEvent.click(screen.getByRole('button',{name:/Bắt đầu trải nghiệm/}));
 await waitFor(()=>expect(api.startProductDemo).toHaveBeenCalledWith(true));
});
