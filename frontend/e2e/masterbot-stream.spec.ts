import { expect, test } from '@playwright/test';

// Browser ReadableStream fixture: send real SSE chunks independently of the terminal result.
// No model or live session writes are invoked.
for(const mobile of [false,true]) test(`${mobile?'Mobile':'Desktop'} Master Bot shows deltas before result, preserves evidence, and reuses validated READY`,async({page},testInfo)=>{
  const pair={pair_id:'p',revision:0,decision:'UNDECIDED',score_unit:'p119_rule_v1',eligibility:{manual_allowed:true,auto_allowed:false,blocked_reasons:[]},baseline:{id:'p',left_id:'l',right_id:'r',primary_group:'A',subgroup:'A1_NAME_PHONE',decision:'REVIEW',score:5,field_evidence:[]},left_record:{record_id:'l',source_system:'CRM',raw_fields:{name:'Synthetic Left'},normalized_fields:{},extra_fields:{}},right_record:{record_id:'r',source_system:'BILL',raw_fields:{name:'Synthetic Right'},normalized_fields:{},extra_fields:{}}};
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    let body:unknown={};
    if(path.endsWith('/auth/me'))body={id:'masterbot-test',email:'masterbot@example.test',role:'STEWARD',csrf_token:'test',capabilities:['read','explain']};
    else if(path.endsWith('/workflow'))body={run_id:'r',workflow_version:2,workflow_state:'DECIDE_REVIEW',workflow_revision:3,decision_revision:1,assessment_id:'a',cluster_revision:0,golden_revision:0,counts:{records:{value:2,unit:'records'},pairs:{value:1,unit:'pairs'}},allowed_actions:[],blockers:[],active_operation:null};
    else if(path.endsWith('/pairs/summary'))body={total:1,needs_review:1,accepted:0,denied:0,kept_separate:0,deferred:0,blocked:0,manual_available:1,auto_available:0,decision_revision:1,groups:[{key:'A',total:1,pending:1,unresolved:1,subgroups:[]}]};
    else if(path.endsWith('/pairs'))body={items:[pair],total:1,offset:0,next_cursor:null};
    else if(path.endsWith('/pairs/p'))body=pair;
    else if(path.endsWith('/runs')||path.endsWith('/upload-sessions'))body={items:[],total:0,next_cursor:null};
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
  });
  await page.addInitScript(()=>{
    const original=window.fetch;
    const testWindow=window as typeof window & {masterBotCount:number;sendMasterBot:(event:string,value:unknown)=>void};
    testWindow.masterBotCount=0;
    window.fetch=async(...args)=>{
      if(String(args[0]).includes('/explanation/stream')){
        testWindow.masterBotCount++;
        return new Response(new ReadableStream<Uint8Array>({start(controller){
          testWindow.sendMasterBot=(event,value)=>{
            controller.enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(value)}\n\n`));
            if(event==='result')controller.close();
          };
        }}),{headers:{'Content-Type':'text/event-stream'}});
      }
      return original(...args);
    };
  });
  if(mobile) await page.setViewportSize({width:390,height:844});
  await page.clock.install();
  await page.goto('/app/sessions/r/decisions/pairs');
  await expect(page.getByRole('region',{name:'Trò chuyện với Master Bot'})).toBeVisible();
  await expect(page.getByRole('region',{name:'Trò chuyện với Master Bot'})).toContainText('Bước 2');
  await expect(page.getByRole('region',{name:'Trò chuyện với Master Bot'})).toContainText('giải thích nhé.');
  const introBubble=page.getByRole('region',{name:'Trò chuyện với Master Bot'});
  await expect(introBubble.locator('mark').filter({hasText:'Đồng ý gộp'})).toBeVisible();
  await expect.poll(async()=>{
    const outer=await introBubble.boundingBox(),paragraph=await introBubble.locator('p').first().boundingBox();
    return outer&&paragraph?outer.y+outer.height-paragraph.y-paragraph.height:999;
  }).toBeLessThan(32);
  expect(await introBubble.evaluate(el=>getComputedStyle(el).transitionProperty)).toContain('opacity');

  await page.screenshot({path:testInfo.outputPath('masterbot-guide.png')});
  await expect(page.getByRole('button',{name:'Master Bot giới thiệu',exact:true})).toHaveAttribute('aria-pressed','true');
  await page.getByRole('button',{name:'Master Bot giới thiệu',exact:true}).click();
  await expect(page.getByRole('region',{name:'Trò chuyện với Master Bot'})).not.toBeVisible();
  await page.getByRole('button',{name:'Mở trò chuyện Master Bot'}).click();
  await expect(page.getByRole('region',{name:'Trò chuyện với Master Bot'})).toContainText('Bước 2');
  expect(await page.evaluate(()=>Reflect.get(window,'masterBotCount'))).toBe(0);
  await page.getByRole('button',{name:'Nhờ Master Bot giải thích cặp p'}).click();
  await expect.poll(()=>page.evaluate(()=>typeof Reflect.get(window,'sendMasterBot'))).toBe('function');
  await page.evaluate(()=>Reflect.get(window,'sendMasterBot')('delta',{text:'Tên đang được đối chiếu'}));
  await expect(page.getByText('Tên đang được đối chiếu',{exact:true})).toBeVisible();
  await expect(page.getByRole('region',{name:'Trò chuyện với Master Bot'})).toBeVisible();
  await page.getByRole('button',{name:'Đối chiếu cặp p',exact:true}).click();
  await expect(page.getByRole('dialog').getByRole('button',{name:'Thu gọn trò chuyện Master Bot'})).toBeVisible();
  await page.evaluate(()=>Reflect.get(window,'sendMasterBot')('delta',{text:' từng phần.'}));
  await expect(page.getByText('Tên đang được đối chiếu từng phần.',{exact:true})).toBeVisible();
  await page.screenshot({path:testInfo.outputPath('masterbot-progressive.png'),fullPage:true});
  await page.evaluate(()=>Reflect.get(window,'sendMasterBot')('result',{status:'READY',explanation:{summary_vi:'Kết quả cuối đã kiểm tra.',attention_fields:['name'],limitations:['Chỉ là gợi ý.']}}));
  await expect(page.getByRole('region',{name:'Trò chuyện với Master Bot'})).toBeVisible();
  await expect(page.getByRole('region',{name:'Trò chuyện với Master Bot'})).toContainText('Kết quả cuối đã kiểm tra.');
  await expect(page.getByRole('region',{name:'Trò chuyện với Master Bot'})).not.toContainText('Bạn nhớ kiểm tra lại họ tên trước khi quyết định gộp nhé.');
  await expect(page.getByRole('region',{name:'Trò chuyện với Master Bot'})).toContainText('Bạn nhớ kiểm tra lại họ tên trước khi quyết định gộp nhé.');
  await expect(page.getByText('Giới hạn',{exact:true})).toHaveCount(0);
  await expect(page.getByText('Chỉ là gợi ý.',{exact:true})).toHaveCount(0);
  await page.screenshot({path:testInfo.outputPath('masterbot-ready.png'),fullPage:true});
  await expect(page.getByRole('dialog').getByText('Synthetic Left',{exact:true})).toBeVisible();
  await page.getByRole('dialog').getByRole('button',{name:'Đóng',exact:true}).click();
  await page.getByRole('button',{name:'Đối chiếu cặp p',exact:true}).click();
  await expect(page.getByRole('region',{name:'Trò chuyện với Master Bot'})).toContainText('Kết quả cuối đã kiểm tra.');
  await page.getByRole('button',{name:'Thu gọn Master Bot',exact:true}).click();
  await expect(page.getByRole('region',{name:'Trò chuyện với Master Bot'})).not.toBeVisible();
  await page.getByRole('button',{name:'Mở trò chuyện Master Bot'}).click();
  await expect(page.getByRole('region',{name:'Trò chuyện với Master Bot'})).toContainText('Kết quả cuối đã kiểm tra.');
  expect(await page.evaluate(()=>Reflect.get(window,'masterBotCount'))).toBe(1);
  await expect(page.getByRole('region',{name:'Trò chuyện với Master Bot'}).locator('mark').filter({hasText:'họ tên'})).toBeVisible();
  const bubbleBounds=await page.getByRole('region',{name:'Trò chuyện với Master Bot'}).boundingBox();
  expect(bubbleBounds!.width).toBeGreaterThan(bubbleBounds!.height);
  await expect(page.getByRole('region',{name:'Trò chuyện với Master Bot'}).getByText('Master Bot',{exact:true})).toHaveCount(0);
  const avatar=page.getByRole('button',{name:'Thu gọn trò chuyện Master Bot'});
  const bounds=await avatar.boundingBox(),viewport=page.viewportSize()!;
  expect(bounds!.x+bounds!.width).toBeLessThanOrEqual(viewport.width);
  expect(viewport.height-bounds!.y-bounds!.height).toBeLessThan(30);
  expect(await avatar.locator('svg').evaluate(el=>getComputedStyle(el).animationName)).not.toBe('none');
  await page.screenshot({path:testInfo.outputPath('masterbot-final.png')});
  await page.clock.fastForward(30001);
  await expect(page.getByRole('region',{name:'Trò chuyện với Master Bot'})).not.toBeVisible();
  await page.getByRole('button',{name:'Mở trò chuyện Master Bot'}).click();
  await expect(page.getByRole('region',{name:'Trò chuyện với Master Bot'})).toContainText('Kết quả cuối đã kiểm tra.');
  expect(await page.evaluate(()=>Reflect.get(window,'masterBotCount'))).toBe(1);
});
