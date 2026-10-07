// 合成库专用浏览器验收；仅访问独立端口 3107，先核对演示教师再允许写虚拟记录。
// 先运行 seed_research_demo.py（空目录 research-demo-v2），启动独立后端8107/前端3107。
// 使用已安装 Playwright；需要时通过 PLAYWRIGHT_MODULE 指定模块路径，不新增产品依赖。
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
const assert = require('node:assert/strict');
async function until(fn, timeout=30000) {let error; const start=Date.now();while(Date.now()-start<timeout){try{await fn();return}catch(e){error=e;await new Promise(r=>setTimeout(r,100));}}throw error;}
function expect(value){return {
 toBeEnabled:({timeout=30000}={})=>until(async()=>assert.ok(await value.isEnabled()),timeout),
 toBeVisible:({timeout=30000}={})=>value.waitFor({state:'visible',timeout}),
 toHaveCount:n=>until(async()=>assert.equal(await value.count(),n)),
 toContainText:(text,{timeout=30000}={})=>until(async()=>assert.ok((await value.innerText()).includes(text)),timeout),
 toHaveURL:pattern=>until(async()=>assert.match(value.url(),pattern)),
 toBeLessThanOrEqual:n=>assert.ok(value<=n),
 toEqual:x=>assert.deepEqual(value,x),
};}

const path = require('path');
(async()=>{
  const root=path.resolve(__dirname,'..');
  const out=path.join(root,'.test-data/research-demo-v2/screenshots');fs.mkdirSync(out,{recursive:true});
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const context=await browser.newContext({viewport:{width:1600,height:1100},deviceScaleFactor:1});
  await context.addInitScript(()=>localStorage.setItem('workspace-scope:homeroom', JSON.stringify({academic_year_id:1,class_id:1})));
  const page=await context.newPage();
  const errors=[], existingAssetWarnings=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',message=>{
    if(message.type()!=='error') return;
    const entry=`${message.text()} ${message.location().url}`;
    // 仓库原本没有 favicon；单独如实记录，不与本功能 API/运行错误混计。
    if(message.location().url==='http://127.0.0.1:3107/favicon.ico' && message.text().includes('404')) existingAssetWarnings.push(entry);
    else errors.push(entry);
  });
  page.on('response',r=>{if(r.url().includes('/api/') && r.status()>=400)errors.push(`${r.status()} ${r.url()}`)});
  await page.goto('http://127.0.0.1:3107/homeroom/research');
  await expect(page.getByText('演示教师（虚拟数据）',{exact:true}).first()).toBeVisible({timeout:60000});
  await expect(page.getByRole('button',{name:'查看前后变化',exact:true})).toBeEnabled({timeout:60000});
  await page.getByRole('button',{name:'查看前后变化',exact:true}).click();
  await expect(page.getByTestId('focus-results')).toBeVisible({timeout:30000});
  await expect(page.getByText('需核查：差距缩小但单科退步',{exact:true})).toBeVisible();
  await expect(page.getByText('暂不可比名单 · 2 人（保留原因供核查）')).toBeVisible();
  await expect(page.getByText('次标签入选 · 主类型：短期下滑型')).toBeVisible();
  await page.screenshot({path:path.join(out,'focus-review.png'),fullPage:true});

  // Real selection resets the old result; scalar values preserve correct percentile units.
  await page.getByLabel('观察指标',{exact:true}).selectOption('subject:数学');
  await expect(page.getByTestId('focus-results')).toHaveCount(0);
  await page.getByRole('button',{name:'查看前后变化',exact:true}).click();
  await expect(page.getByTestId('focus-results')).toBeVisible();
  await expect(page.getByText('相对位置上升 24 个百分点',{exact:true})).toBeVisible();
  await page.getByText('历次走势与证据',{exact:true}).first().click();
  await expect(page.locator('.recharts-surface').first()).toBeVisible();
  await page.screenshot({path:path.join(out,'score-evidence.png'),fullPage:true});
  // Delay a genuine API response; a changed filter invalidates that in-flight result.
  let release, started;
  const holding=new Promise(r=>release=r), startedPromise=new Promise(r=>started=r);
  await page.route('**/research/focus/outcome?*',async route=>{
    const response=await route.fetch();started();await holding;
    try {await route.fulfill({response})}catch{} // browser has cancelled the old request
  });
  await page.getByLabel('观察指标',{exact:true}).selectOption('total:主三门');
  await page.getByRole('button',{name:'查看前后变化',exact:true}).click();
  await startedPromise;
  await page.getByLabel('观察指标',{exact:true}).selectOption('subject:数学');
  release();await page.unroute('**/research/focus/outcome?*');
  await expect(page.getByTestId('focus-results')).toHaveCount(0);
  await expect(page.getByRole('button',{name:'查看前后变化',exact:true})).toBeEnabled();
  // Current issues are a different selected population, never falsely marked historical.
  await page.getByRole('button',{name:'现在的问题学生',exact:true}).click();
  await expect(page.getByTestId('focus-results')).toHaveCount(0);
  await page.getByRole('button',{name:'查看前后变化',exact:true}).click();
  await expect(page.getByTestId('focus-results')).toContainText('当前主／次标签选人');
  // Homework equal-length windows use actual batch coverage and no automatic success.
  await page.getByRole('button',{name:'当时的问题学生',exact:true}).click();
  await page.getByLabel('关注问题',{exact:true}).selectOption('作业风险型');
  await page.getByRole('button',{name:'查看前后变化',exact:true}).click();
  await expect(page.getByTestId('focus-results')).toContainText('缺交 4 次');
  await expect(page.getByTestId('focus-results')).toContainText('缺交 1 次');
  await page.screenshot({path:path.join(out,'homework-review.png'),fullPage:true});
  // Own-baseline follow-up review, including pending facts and closed records.
  await page.getByRole('tab',{name:'已建档跟进复查'}).click();
  await expect(page.getByTestId('follow-up-review')).toContainText('开始跟进之后尚无可比考试',{timeout:30000});
  await expect(page.getByTestId('follow-up-review')).toContainText('前 66% → 前 42%');
  await expect(page.getByTestId('follow-up-review')).toContainText('相对位置上升 24 个百分点');
  await expect(page.getByTestId('follow-up-review')).toContainText('相对位置下降 4 个百分点');
  await page.getByRole('button',{name:'全部记录',exact:true}).click();
  await expect(page.getByTestId('follow-up-review')).toContainText('教师已关闭');
  await page.screenshot({path:path.join(out,'follow-up-review.png'),fullPage:true});

  // Tabs work by keyboard as well as pointer.
  await page.getByRole('tab',{name:'已建档跟进复查'}).focus();
  await page.keyboard.press('Home');
  assert.equal(await page.getByRole('tab',{name:'问题学生回看'}).getAttribute('aria-selected'),'true');
  await page.keyboard.press('End');
  await expect(page.getByTestId('follow-up-review')).toContainText('前 66% → 前 42%');
  await expect(page.getByTestId('follow-up-review')).toContainText('相对位置上升 24 个百分点');
  await expect(page.getByTestId('follow-up-review')).toContainText('相对位置下降 4 个百分点');
  // Real editable profile is reachable for exactly this person.
  const link=page.getByRole('link',{name:'查看／记录观察',exact:true}).first();
  const href=await link.getAttribute('href');
  await link.click();
  await expect(page).toHaveURL(new RegExp(href.replace(/[?]/g,'\\?')));
  await expect(page.getByText('虚拟数据：用于关注回看与复查验收').first()).toBeVisible({timeout:60000});
  // Complete the action loop using only a new synthetic note; remove it afterwards.
  await page.goto('http://127.0.0.1:3107/homeroom/profile?person_id=3');
  const newButton=page.getByRole('button',{name:'新建干预',exact:true});
  await expect(newButton).toBeVisible({timeout:60000});await newButton.click();
  await page.getByPlaceholder('问题（必填，例如：主三门名次连续下滑）').fill('虚拟验收：数学短板复查');
  await page.getByPlaceholder('学科范围（可选，如：数学；留空=不区分学科）').fill('数学');
  await page.locator('select').filter({has:page.locator('option', {hasText:'目标指标：数学（年级前百分位）'})}).selectOption('subject:数学');
  await page.getByPlaceholder('措施（可选，例如：每周一次错题面批）').fill('虚拟验收面批');
  await page.getByPlaceholder('基线值（可选，按百分数填：40 = 年级前 40%；留空=自动取最近一场）').fill('70');
  await page.getByLabel('开始日',{exact:true}).fill('2026-09-16');
  await page.getByLabel('计划复查日',{exact:true}).fill('2026-10-07');

  const saved=page.waitForResponse(r=>r.request().method()==='POST' && r.url().includes('/students/3/notes')).catch(e=>({error:e}));
  await page.getByRole('button',{name:'创建干预',exact:true}).click();
  const savedResponse=await saved;
  if(savedResponse.error){await page.screenshot({path:path.join(out,'profile-submit-debug.png'),fullPage:true});console.log(await page.locator('body').innerText());throw savedResponse.error;}
  const created=await savedResponse.json();assert.equal(created.problem,'虚拟验收：数学短板复查');
  try {
    await page.goto('http://127.0.0.1:3107/homeroom/research');
    await page.getByRole('tab',{name:'已建档跟进复查'}).click();
    await expect(page.getByTestId('follow-up-review')).toContainText('虚拟验收：数学短板复查');
    await expect(page.getByTestId('follow-up-review')).toContainText('前 70% → 前 76%');
    const row=page.getByRole('row').filter({hasText:'虚拟验收：数学短板复查'});
    await row.getByRole('link',{name:'查看／记录观察'}).click();
    await page.getByRole('button',{name:'标记完成并关闭',exact:true}).click();
    await expect(page.getByText('已关闭',{exact:true}).first()).toBeVisible();
    await page.goto('http://127.0.0.1:3107/homeroom/research');
    await page.getByRole('tab',{name:'已建档跟进复查'}).click();
    await page.getByRole('button',{name:'全部记录',exact:true}).click();
    await expect(page.getByRole('row').filter({hasText:'虚拟验收：数学短板复查'})).toContainText('教师已关闭');
  } finally {
    const deleted=await context.request.delete(`http://127.0.0.1:3107/api/v1/homeroom/notes/${created.id}?academic_year_id=1`);
    assert.ok(deleted.ok());
  }
  // Narrow viewport: page may not overflow; tables scroll within their own wrappers.
  await page.setViewportSize({width:390,height:844});
  await page.goto('http://127.0.0.1:3107/homeroom/research');
  await expect(page.getByRole('button',{name:'查看前后变化',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'查看前后变化',exact:true}).click();
  await expect(page.getByTestId('focus-results')).toBeVisible();
  const sizes=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
  console.log('mobile-width',sizes);
  expect(sizes.scroll).toBeLessThanOrEqual(sizes.width);
  await page.screenshot({path:path.join(out,'focus-mobile.png'),fullPage:true});
  expect(errors).toEqual([]);
  fs.writeFileSync(path.join(out,'verification.json'),JSON.stringify({checks:['historical-fixed-members','secondary-tags','false-improvement-guard','missing-values','selection-reset','in-flight-request-race','keyboard-tabs','create-followup','review-followup','close-followup','synthetic-note-cleanup','percentile-units','timeline','current-vs-historical','homework-coverage','own-baseline-followups','closed-records','editable-profile','mobile-overflow'],errors,existingAssetWarnings,sizes},null,2));
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
