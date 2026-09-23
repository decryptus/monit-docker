/* Browser-only development test. No Node dependency is shipped in the image. */
'use strict';
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const http = require('node:http');

const root = path.resolve(__dirname, '../static');
const output = path.resolve(process.env.UI_SCREENSHOTS || 'ui/test-results');
fs.mkdirSync(output, {recursive: true});
const types = {'.html': 'text/html', '.css': 'text/css', '.js': 'application/javascript'};
const server = http.createServer((request, response) => {
  const filename = {'/': 'index.html', '/app.css': 'app.css', '/app.js': 'app.js'}[request.url];
  if (!filename) { response.writeHead(404).end(); return; }
  response.setHeader('Content-Type', types[path.extname(filename)]);
  response.end(fs.readFileSync(path.join(root, filename)));
});

async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const names = ['web-frontend', 'api-service', 'postgres-primary', 'redis-cache', 'nightly-backup'];
    let state = {
      api_version: 1, ready: true, running: false, last_cycle_success: true,
      last_success_at: 1790129400, last_cycle_finished_at: 1790129400, age_seconds: 2,
      last_error_code: null, cycles_total: 2841, errors_total: 0,
      actions: {executed: 3, cooldown: 8, pending: 1, 'dry-run': 0},
      containers: names.map((name, i) => ({id: String(i + 1).repeat(64), name,
        status: i === 4 ? 'exited' : 'running', cpu_percent: [4.2, 23.6, 1.8, .4, null][i],
        mem_usage: [84, 246, 512, 12, null][i] === null ? null : [84, 246, 512, 12][i] * 1048576,
        mem_limit: 1073741824, mem_percent: [8.2, 24, 50, 1.2, null][i]})),
      manual_actions: {enabled: true, allowed_states: {start: ['created', 'exited'],
        stop: ['running', 'restarting'], restart: ['running']}, recent: []}
    };
    let offline = false, posts = 0, ambiguous = false;
    await page.route('**/v1/status', route => offline ? route.abort() : route.fulfill({json: state}));
    await page.route('**/v1/actions', async route => {
      posts++;
      const submitted = route.request().postDataJSON();
      assert.equal(route.request().headers()['content-type'], 'application/json');
      assert.equal(submitted.action, 'restart');
      assert.match(submitted.request_id, /^[0-9a-f]{32}$/);
      if (ambiguous) return route.fulfill({status: 504, body: 'timeout'});
      const record = {...submitted, status: 'queued', submitted_at: 1790129400, error: null};
      state.manual_actions.recent.push(record);
      await route.fulfill({status: 202, json: record});
    });
    await page.goto(base);
    await page.waitForFunction(() => document.getElementById('collection').textContent === 'Healthy');
    await page.locator('#auto').uncheck();
    assert.equal(await page.locator('.container-row').count(), 5);
    assert.equal(await page.locator('.container-row button:visible').count(), 9);
    // Protected containers stay visible, with disabled controls for every state.
    state.containers[2].manual_actions_protected = true;
    state.containers[4].manual_actions_protected = true;
    await page.locator('#refresh').click();
    await page.waitForFunction(() => document.querySelectorAll('.protection:not([hidden])').length === 2);
    assert.equal(await page.locator('.container-row').count(), 5);
    for (const name of ['Stop postgres-primary', 'Restart postgres-primary', 'Start nightly-backup']) {
      assert.equal(await page.getByRole('button', {name, exact: true}).isDisabled(), true);
    }
    assert.equal(await page.getByRole('button', {name: 'Restart api-service', exact: true}).isEnabled(), true);
    await page.screenshot({path: path.join(output, 'ui-desktop.png'), fullPage: true});
    for (const width of [360, 390, 720, 1024]) {
      await page.setViewportSize({width, height: 844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflow at ${width}`);
      if (width === 390) await page.screenshot({path: path.join(output, 'ui-mobile.png'), fullPage: true});
    }
    // Literal text, unknown values, >100% CPU, keyboard focus and filtering.
    state.containers[0].name = '<img src=x onerror=alert(1)>-long-container-name-'.repeat(3);
    state.containers[0].cpu_percent = 230;
    await page.locator('#refresh').click();
    await page.waitForFunction(() => document.querySelector('.identity h3').textContent.startsWith('<img'));
    assert.equal(await page.locator('.container-row img').count(), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.locator('#search').fill('api-service');
    assert.equal(await page.locator('.container-row:visible').count(), 1);
    await page.getByRole('button', {name: 'Restart api-service', exact: true}).click();
    await page.getByRole('button', {name: 'Cancel', exact: true}).click();
    assert.equal(posts, 0);
    // Protection appearing while confirmation is open invalidates that dialog too.
    await page.getByRole('button', {name: 'Restart api-service', exact: true}).click();
    state.containers[1].manual_actions_protected = true;
    await page.evaluate(() => refresh());
    assert.equal(await page.locator('#confirm-action').isDisabled(), true);
    await page.getByRole('button', {name: 'Cancel', exact: true}).click();
    assert.equal(posts, 0);
    delete state.containers[1].manual_actions_protected;
    await page.locator('#refresh').click();
    await page.waitForFunction(() => !document.querySelector('[aria-label="Restart api-service"]').disabled);
    await page.getByRole('button', {name: 'Restart api-service', exact: true}).click();
    await page.locator('#confirm-action').click();
    await page.waitForFunction(() => document.querySelector('#activity li')?.textContent.includes('queued'));
    assert.equal(posts, 1);
    assert.equal(await page.getByRole('button', {name: 'Restart api-service', exact: true}).isDisabled(), true);
    state.manual_actions.recent[0].status = 'succeeded';
    await page.locator('#refresh').click();
    await page.waitForFunction(() => document.querySelector('#activity li').textContent.includes('succeeded'));
    // A gateway timeout is ambiguous: never retry or enable another mutation.
    ambiguous = true;
    await page.getByRole('button', {name: 'Restart api-service', exact: true}).click();
    await page.locator('#confirm-action').click();
    await page.waitForFunction(() => document.getElementById('notice').textContent.includes('unconfirmed'));
    assert.equal(posts, 2);
    assert.equal(await page.getByRole('button', {name: 'Restart api-service', exact: true}).isDisabled(), true);
    // A new page clears local display state; API remains authoritative.
    await page.reload();
    await page.waitForFunction(() => document.getElementById('collection').textContent === 'Healthy');
    await page.locator('#auto').uncheck();
    state.ready = false; state.last_error_code = 170;
    await page.locator('#refresh').click();
    await page.waitForFunction(() => document.getElementById('collection').textContent === 'Not ready');
    assert.equal(await page.locator('.container-row').count(), 0);
    state.ready = true; state.last_error_code = null;
    offline = true;
    await page.locator('#refresh').click();
    await page.waitForFunction(() => document.getElementById('collection').textContent === 'Offline');
    assert.equal(await page.locator('.container-row').count(), 0);
    offline = false; delete state.manual_actions;
    await page.locator('#refresh').click();
    await page.waitForFunction(() => document.getElementById('collection').textContent === 'Healthy');
    assert.equal(await page.locator('.container-row button:visible').count(), 0);
    assert.equal(posts, 2);
    assert.deepEqual(errors, []);
    console.log('Browser checks passed: protection, mobile/desktop, XSS text, confirmations, queue, stale/offline, old API.');
  } finally { await browser.close(); server.close(); }
}
main().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
