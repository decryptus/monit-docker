'use strict';
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const root = path.resolve(__dirname, '../static');
const output = path.resolve(process.env.UI_SCREENSHOTS || 'ui/test-results');
const FILES = {'/logs': ['logs.html', 'text/html'], '/logs.js': ['logs.js', 'application/javascript'], '/app.css': ['app.css', 'text/css']};
const server = http.createServer((req, res) => {
  const file = FILES[req.url];
  if (!file) return res.writeHead(404).end();
  res.setHeader('Content-Type', file[1]); res.end(fs.readFileSync(path.join(root, file[0])));
});
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1050}, acceptDownloads:true});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    let requests = 0, mode = 200, lastParams, exportParams, actionMode = 'paged';
    let releaseAction, actionHeld;
    const event = {schema_version:2,event_id:'1234',timestamp:'2026-09-23T18:30:00Z',host:'docker-host',category:'action',event:'completed',source:'manual',actor:'alice',container_name:'api-service',action:'restart',result:'succeeded',duration_ms:180,correlation_id:'request-1234',reason:null};
    const examples = [
      event,
      {...event, event_id:'queued', event:'queued', result:'pending'},
      {...event, event_id:'started', event:'started', result:'pending'},
      {...event, event_id:'failed', result:'failed', reason:'execution_failed'},
      {...event, event_id:'skipped', event:'skipped', result:'skipped', source:'automatic', actor:'cron', reason:'maintenance'},
      {...event, event_id:'simulation', event:'skipped', result:'simulated', reason:'dry-run', correlation_id:null},
      {...event, event_id:'accepted', category:'notification', event:'accepted', result:'accepted', source:'redis', action:null}
    ];
    event.container_id = 'a'.repeat(64);
    for (const example of examples) example.container_id = event.container_id;
    await page.route('**/v1/audit?*', async route => {
      requests++;
      if (mode !== 200) return route.fulfill({status:mode,json:{error:'denied'}});
      const params = new URL(route.request().url()).searchParams;
      lastParams = params;
      if (params.has('correlation_id')) {
        assert.equal(params.get('category'), 'action');
        assert.equal(params.has('result'), false, 'action history must include stages hidden by journal filters');
        assert.equal(params.has('source'), false);
        assert.equal(params.has('container'), false);
        if (actionMode === 'held') await new Promise(resolve => { releaseAction = resolve; actionHeld(); });
        if (actionMode === 'expired') return route.fulfill({status:410,json:{error:'cursor_expired'}});
        if (actionMode === 'slow') await new Promise(resolve => setTimeout(resolve, 350));
        const stages = [
          {...event, event_id:'action-queued', event:'queued', result:'pending', duration_ms:null},
          {...event, event_id:'action-started', event:'started', result:'pending', duration_ms:null},
          {...event, event_id:'action-completed', event:'completed', result:'failed', reason:'<img src=x onerror=alert(1)>'}
        ];
        const cursor = params.get('cursor');
        const records = actionMode === 'empty' || (actionMode === 'sparse' && !cursor) ? []
          : actionMode.startsWith('many') ? Array.from({length:actionMode === 'many-tail' && cursor === '4' ? 50 : 100}, (_, i) => ({...event,event_id:`${cursor || 'first'}-${i}`}))
          : cursor ? stages.slice(0,1) : stages.slice(1).reverse();
        const next = actionMode === 'empty' || (actionMode === 'many-tail' && cursor === '5') ? null : actionMode.startsWith('many') ? String(Number(cursor || 0) + 1) : cursor ? null : 'action-older';
        return route.fulfill({json:{records,next_cursor:next,page_cursor:cursor || 'action-first'}}).catch(() => {});
      }
      const older = params.get('cursor') === 'older';
      if (params.get('container') === 'slow') {
        await new Promise(resolve => setTimeout(resolve, 250));
      }
      const matching = examples.filter(record => ['source', 'result', 'category'].every(key => !params.has(key) || params.get(key) === record[key]));
      await route.fulfill({json:{records:params.get('container') === 'absent' ? [] : older ? [{...event, event_id:'older',result:'failed',reason:'execution_failed',container_name:'<img src=x onerror=alert(1)>'}] : matching,next_cursor:older ? null : 'older',page_cursor:older ? 'older' : 'first',scanned_bytes:1000,limit:100}}).catch(() => {});
    });
    await page.route('**/v1/audit/export?*', route => {
      const params = new URL(route.request().url()).searchParams;
      exportParams = params;
      assert.equal(params.get('cursor'), 'first');
      return route.fulfill({contentType:'text/csv',body:'actor,result\r\nalice,succeeded\r\n'});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/logs`);
    await page.waitForSelector('.log-event');
    const cards = page.locator('.log-event');
    assert.match(await cards.nth(0).textContent(), /Restart completed/);
    assert.deepEqual(await cards.nth(0).locator('.log-tag').allTextContents(), ['Host: docker-host', 'Container: api-service', 'Manual action', 'Actor: alice', 'View action']);
    assert.equal(await cards.nth(0).getAttribute('data-tone'), 'success');
    assert.equal(await cards.nth(1).locator('.badge').textContent(), 'Queued');
    assert.equal(await cards.nth(2).locator('.badge').textContent(), 'In progress');
    assert.equal(await cards.nth(3).getAttribute('data-tone'), 'danger');
    assert.equal(await cards.nth(3).locator('.log-reason').isVisible(), true);
    assert.match(await cards.nth(3).locator('.log-reason').textContent(), /execution_failed/);
    assert.equal(await cards.nth(4).getAttribute('data-tone'), 'warning');
    assert.match(await cards.nth(4).textContent(), /Automatic action/);
    assert.equal(await cards.nth(5).getAttribute('data-tone'), 'secondary');
    assert.equal(await cards.nth(6).getAttribute('data-tone'), 'info', 'accepted must not imply delivery');
    assert.match(await cards.nth(6).locator('h3').textContent(), /Notification accepted/);
    assert.equal(await page.locator('.log-event[role=alert]').count(), 0, 'history must not announce every row as a live alert');
    const before = requests; await page.waitForTimeout(300); assert.equal(requests,before,'journal must not poll');
    fs.mkdirSync(output,{recursive:true});
    await page.screenshot({path:path.join(output,'journal-desktop.png'),fullPage:true});
    for (const width of [360,390,720]) {
      await page.setViewportSize({width,height:844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true,`overflow at ${width}`);
      if (width===390) await page.screenshot({path:path.join(output,'journal-mobile.png'),fullPage:true});
    }
    const loaded = () => page.waitForFunction(() => !document.getElementById('log-notice').textContent.startsWith('Loading'));
    const actionLoaded = () => page.waitForFunction(() => !document.getElementById('action-notice').textContent.startsWith('Loading'));
    assert.equal(await cards.nth(5).locator('.log-view-action').count(), 0, 'legacy records without an ID cannot be grouped');
    assert.equal(await cards.nth(6).locator('.log-view-action').count(), 0, 'notifications are not action histories');
    await cards.nth(3).locator('.badge').click();
    await loaded();
    actionMode = 'held';
    const held = new Promise(resolve => { actionHeld = resolve; });
    await page.locator('#log-events .log-view-action').first().click();
    await held;
    assert.equal(await page.locator('#action-events .log-event').count(), 1, 'show matching journal events before the server responds');
    assert.equal(await page.locator('#action-events .badge').textContent(), 'Failed');
    assert.match(await page.locator('#action-notice').textContent(), /Showing previously loaded events/);
    assert.equal(await page.locator('#older-action').isDisabled(), true);
    actionMode = 'paged';
    releaseAction();
    await actionLoaded();
    assert.equal(lastParams.get('correlation_id'), 'request-1234');
    assert.deepEqual(await page.locator('#action-events .badge').allTextContents(), ['In progress', 'Failed']);
    assert.equal(await page.locator('#action-events img').count(), 0);
    assert.match(await page.locator('#action-events').textContent(), /Duration: 180 ms/);
    assert.match(await page.locator('#action-help').textContent(), /missing stages/);
    assert.equal(await page.locator('#action-events button').count(), 0);
    const actionRequests = requests;
    await page.waitForTimeout(300);
    assert.equal(requests, actionRequests, 'action history must not scan or poll automatically');
    await page.locator('#older-action').click();
    await actionLoaded();
    assert.deepEqual(await page.locator('#action-events .badge').allTextContents(), ['Queued', 'In progress', 'Failed']);
    assert.equal(await page.locator('#older-action').isDisabled(), true);
    await page.setViewportSize({width:1440,height:1050});
    await page.screenshot({path:path.join(output,'action-history-desktop.png'),fullPage:true});
    for (const width of [360,390,720]) {
      await page.setViewportSize({width,height:844});
      assert.equal(await page.locator('#action-dialog').evaluate(el => el.scrollWidth <= el.clientWidth),true,`action overflow at ${width}`);
      if (width===390) await page.screenshot({path:path.join(output,'action-history-mobile.png'),fullPage:true});
    }
    await page.keyboard.press('Escape');
    await page.waitForSelector('#action-dialog', {state:'hidden'});
    assert.equal(await page.locator('#log-events .log-view-action').first().evaluate(el => el === document.activeElement), true);
    assert.equal(await page.locator('[name=result]').inputValue(), 'failed');
    for (const scenario of ['sparse', 'expired', 'empty', 'many', 'many-tail']) {
      actionMode = scenario;
      await page.locator('#log-events .log-view-action').first().click();
      await actionLoaded();
      if (scenario === 'sparse') {
        assert.equal(await page.locator('#action-events .log-event').count(), 0);
        assert.equal(await page.locator('#older-action').isEnabled(), true);
        await page.locator('#older-action').click();
        await actionLoaded();
        assert.equal(await page.locator('#action-events .log-event').count(), 1);
      } else if (scenario === 'expired') {
        assert.match(await page.locator('#action-notice').textContent(), /expired.*Refresh action/);
        assert.match(await page.locator('#action-notice').textContent(), /incomplete or outdated/);
        assert.equal(await page.locator('#action-events .log-event').count(), 1, 'failed refresh retains a clearly labeled preview');
        actionMode = 'paged';
        await page.locator('#refresh-action').click();
        await actionLoaded();
        assert.equal(lastParams.has('cursor'), false);
        assert.equal(await page.locator('#action-events .log-event').count(), 2);
      } else if (scenario === 'empty') {
        assert.match(await page.locator('#action-notice').textContent(), /No matching action events/);
      } else {
        for (let i=0;i<(scenario === 'many-tail' ? 5 : 4);i++) { await page.locator('#older-action').click(); await actionLoaded(); }
        assert.equal(await page.locator('#action-events .log-event').count(), 500);
        assert.equal(await page.locator('#older-action').isDisabled(), true);
        assert.match(await page.locator('#action-notice').textContent(), /Display limit/);
      }
      await page.locator('#close-action').click();
      await page.waitForSelector('#action-dialog', {state:'hidden'});
    }
    actionMode = 'slow';
    await page.locator('#log-events .log-view-action').first().click();
    await page.waitForTimeout(25);
    await page.locator('#close-action').click();
    await page.waitForSelector('#action-dialog', {state:'hidden'});
    actionMode = 'empty';
    await page.locator('#log-events .log-view-action').first().click();
    await actionLoaded();
    await page.waitForTimeout(400);
    assert.equal(await page.locator('#action-events .log-event').count(), 0, 'closed history responses must not replace a reopened view');
    await page.locator('#close-action').click();
    await page.waitForSelector('#action-dialog', {state:'hidden'});
    await page.locator('#reset-logs').click();
    await loaded();
    assert.equal(await cards.nth(6).locator('[data-kind="neutral"]').evaluate(el => el.tagName), 'SPAN', 'unsupported sources must not offer an invalid filter');
    await cards.nth(0).locator('.log-target').focus();
    await page.keyboard.press('Enter');
    await loaded();
    assert.equal(lastParams.get('container'), event.container_id, 'prefer the complete container ID');
    assert.equal(await page.locator('[name=container]').inputValue(), event.container_id);
    assert.equal(await page.locator('[data-filter=container]').evaluate(el => el === document.activeElement), true);
    await cards.nth(0).locator('[data-kind=manual]').click();
    await loaded();
    await cards.nth(1).locator('.badge').click();
    await loaded();
    assert.equal(await cards.count(), 2, 'pending includes queued and started');
    assert.equal(lastParams.get('source'), 'manual');
    assert.equal(lastParams.get('result'), 'pending');
    assert.equal(await page.locator('#active-log-filters button').count(), 3);
    assert.match(await page.locator('[data-filter=result]').textContent(), /queued or in progress/);
    assert.equal(await cards.first().locator('.badge').getAttribute('aria-pressed'), 'true');
    const filteredDownload = page.waitForEvent('download');
    await page.locator('#export-jsonl').click();
    await filteredDownload;
    assert.equal(exportParams.get('container'), event.container_id);
    assert.equal(exportParams.get('source'), 'manual');
    assert.equal(exportParams.get('result'), 'pending');
    await page.setViewportSize({width:360,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true,'active filters must wrap on mobile');
    await page.screenshot({path:path.join(output,'journal-filters-mobile.png'),fullPage:true});
    await page.locator('#older-logs').click();
    await loaded();
    await page.locator('[data-filter=result]').click();
    await loaded();
    assert.equal(lastParams.has('cursor'), false, 'changing filters restarts pagination');
    assert.equal(lastParams.has('result'), false);
    assert.equal(lastParams.get('source'), 'manual');
    assert.equal(await page.locator('#log-page').textContent(), 'Page 1');
    assert.equal(await page.locator('[name=result]').inputValue(), '');
    await page.locator('[name=source]').selectOption('automatic');
    await cards.first().locator('.badge').click();
    await loaded();
    assert.equal(lastParams.get('source'), 'manual', 'quick filters use the applied view, not an unsubmitted draft');
    assert.equal(await page.locator('[name=source]').inputValue(), 'manual');
    await page.locator('#reset-logs').click();
    await loaded();
    assert.equal(await page.locator('#active-log-filters').isHidden(), true);
    assert.equal(lastParams.size, 0);
    await page.locator('[name=since]').fill('2026-09-01T10:30');
    await page.locator('[name=category]').selectOption('action');
    await page.locator('#log-filters').evaluate(form => form.requestSubmit());
    await loaded();
    const since = lastParams.get('since');
    await cards.first().locator('[data-kind=manual]').click();
    await loaded();
    assert.equal(lastParams.get('since'), since);
    assert.equal(lastParams.get('category'), 'action');
    assert.equal(await page.locator('[name=since]').inputValue(), '2026-09-01T10:30');
    await page.locator('#reset-logs').click();
    await loaded();
    await page.locator('#older-logs').click();
    await page.waitForFunction(() => document.getElementById('log-page').textContent==='Page 2');
    assert.equal(await page.locator('.log-event img').count(),0);
    assert.match(await page.locator('.log-event .log-target').textContent(), /<img/);
    assert.equal(await page.locator('#older-logs').isDisabled(),true);
    await page.locator('#previous-logs').click();
    await page.waitForFunction(() => document.getElementById('log-page').textContent==='Page 1');
    const downloadPromise = page.waitForEvent('download');
    await page.locator('#export-csv').click();
    const download = await downloadPromise;
    assert.equal(download.suggestedFilename(),'monit-docker-events.csv');
    assert.match(fs.readFileSync(await download.path(),'utf8'), /alice,succeeded/);
    await page.locator('[name=container]').fill('absent');
    await page.locator('#log-filters').evaluate(form => form.requestSubmit());
    await page.waitForSelector('#log-empty:not([hidden])');
    assert.match(await page.locator('#log-notice').textContent(), /continue searching/);
    assert.equal(await page.locator('#older-logs').isEnabled(),true);
    assert.equal(await page.locator('#export-csv').isDisabled(),true);
    await page.locator('[name=container]').fill('slow');
    await page.locator('#log-filters').evaluate(form => form.requestSubmit());
    await page.waitForTimeout(25);
    await page.locator('[name=container]').fill('absent');
    await page.locator('#log-filters').evaluate(form => form.requestSubmit());
    await page.waitForTimeout(350);
    assert.equal(await page.locator('.log-event').count(),0,'stale response must not replace current filters');
    for (const status of [401,403,404,410,503]) {
      mode=status;
      await page.locator('#refresh-logs').click();
      await page.waitForFunction(() => !document.getElementById('log-notice').textContent.startsWith('Loading'));
      assert.equal(await page.locator('.log-event').count(),0);
      assert.equal(await page.locator('#export-jsonl').isDisabled(),true);
    }
    assert.deepEqual(errors,[]);
    console.log('Journal browser checks passed: mobile, no polling, filters, cursor history, downloads, stale requests, XSS and error states.');
  } finally {await browser.close();server.close();}
}
main().catch(error=>{console.error(error);server.close();process.exitCode=1;});
