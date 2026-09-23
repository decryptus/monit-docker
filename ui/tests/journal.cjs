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
    let requests = 0, mode = 200;
    const event = {schema_version:2,event_id:'1234',timestamp:'2026-09-23T18:30:00Z',host:'docker-host',category:'action',event:'completed',source:'manual',actor:'alice',container_name:'api-service',action:'restart',result:'succeeded',duration_ms:180,correlation_id:'request-1234',reason:null};
    await page.route('**/v1/audit?*', async route => {
      requests++;
      if (mode !== 200) return route.fulfill({status:mode,json:{error:'denied'}});
      const params = new URL(route.request().url()).searchParams;
      const older = params.get('cursor') === 'older';
      if (params.get('container') === 'slow') {
        await new Promise(resolve => setTimeout(resolve, 250));
      }
      await route.fulfill({json:{records:params.get('container') === 'absent' ? [] : [older ? {...event, event_id:'older',result:'failed',reason:'execution_failed',container_name:'<img src=x onerror=alert(1)>'} : event],next_cursor:older ? null : 'older',page_cursor:older ? 'older' : 'first',scanned_bytes:1000,limit:100}}).catch(() => {});
    });
    await page.route('**/v1/audit/export?*', route => {
      const params = new URL(route.request().url()).searchParams;
      assert.equal(params.get('cursor'), 'first');
      return route.fulfill({contentType:'text/csv',body:'actor,result\r\nalice,succeeded\r\n'});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/logs`);
    await page.waitForSelector('.log-event');
    const before = requests; await page.waitForTimeout(300); assert.equal(requests,before,'journal must not poll');
    fs.mkdirSync(output,{recursive:true});
    await page.screenshot({path:path.join(output,'journal-desktop.png'),fullPage:true});
    for (const width of [360,390,720]) {
      await page.setViewportSize({width,height:844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true,`overflow at ${width}`);
      if (width===390) await page.screenshot({path:path.join(output,'journal-mobile.png'),fullPage:true});
    }
    await page.locator('#older-logs').click();
    await page.waitForFunction(() => document.getElementById('log-page').textContent==='Page 2');
    assert.equal(await page.locator('.log-event img').count(),0);
    assert.match(await page.locator('.log-event h3').textContent(), /<img/);
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
