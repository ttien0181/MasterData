import {expect,it} from 'vitest';
import {completenessAverage,metricPercent,metricExplanation} from './metricInsights';
import type {DataProfile} from './workflowContracts';
it('uses real denominators and leaves unavailable or inconsistent figures unknown',()=>{
 expect(metricPercent(25,100)).toBe('25%');expect(metricPercent(0,100)).toBe('0%');
 for(const pair of [[0,0],[2,1],[undefined,20],[1,undefined],[-1,10],[NaN,10]])expect(metricPercent(...pair as [number,number])).toBe('—');
 expect(completenessAverage({record_count:10,field_completeness:{name:{present:10,missing:0}}} as unknown as DataProfile)).toBe('—');
 const field_completeness=Object.fromEntries(['name','phone','email','address','dob','government_id'].map(k=>[k,{present:5,missing:5}]));
 expect(completenessAverage({record_count:10,field_completeness} as unknown as DataProfile)).toBe('50%');
 expect(metricExplanation('Email','—','Thông tin đã điền.')).toContain('chưa có số liệu');
});
