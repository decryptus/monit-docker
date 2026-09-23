/* Acceptance against pulled images and a real disposable Docker container. */
'use strict';
const {chromium, request} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');

const CONFIG = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const PHASE = process.argv[3];
const TIMEOUT = 90000;
const ACTION_COOLDOWN_MS = 1100;
const PRIVATE_PATHS = ['/metrics', '/healthz', '/readyz'];
const PROTECTED_PATHS = ['/', '/app.js', '/app.css', '/v1/status'];
const VIEWPORTS = [{width: 1440, height: 1100}, {width: 390, height: 844}];
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function inspectDemo() {
  const item = JSON.parse(execFileSync('docker', ['inspect', CONFIG.demo_id], {encoding: 'utf8'}))[0];
  assert.equal(item.Id, CONFIG.demo_id);
  assert.equal(item.Config.Labels['monit-docker.fixture'], 'ui-release-acceptance');
  return item.State;
}
async function status(context) {
  const response = await context.request.get(`${CONFIG.origin}/v1/status`);
  assert.equal(response.status(), 200);
  return response.json();
}
async function untilStatus(context, predicate) {
  const deadline = Date.now() + TIMEOUT;
  while (Date.now() < deadline) {
    const response = await context.request.get(`${CONFIG.origin}/v1/status`);
    if ([502, 503].includes(response.status())) { await delay(500); continue; }
    assert.equal(response.status(), 200);
    const data = await response.json();
    if (predicate(data)) return data;
    await delay(500);
  }
  throw new Error('Timed out waiting for the published agent state');
}
async function refresh(page) {
  await page.locator('#refresh').click();
  await page.waitForFunction(() => !document.getElementById('refresh').disabled);
  await page.waitForFunction(() => document.getElementById('connection').textContent === 'Agent connected');
}
async function perform(page, context, action, expectedState) {
  await delay(ACTION_COOLDOWN_MS); // Honor the test overlay's one-second cooldown.
  await untilStatus(context, data => data.ready);
  await refresh(page);
  await page.getByRole('button', {name: `${action} ${CONFIG.demo_name}`, exact: true}).click();
  const received = page.waitForResponse(response => response.url() === `${CONFIG.origin}/v1/actions`
    && response.request().method() === 'POST');
  await page.locator('#confirm-action').click();
  const response = await received;
  assert.equal(response.status(), 202);
  const accepted = await response.json();
  assert.equal(accepted.container_id, CONFIG.demo_id);
  const finished = await untilStatus(context, data => {
    const result = data.manual_actions.recent.find(item => item.request_id === accepted.request_id);
    if (result?.status === 'failed') throw new Error(`Manual ${action} failed: ${result.error}`);
    return result?.status === 'succeeded' && data.ready && data.containers[0]?.status === expectedState;
  });
  assert.equal(inspectDemo().Status, expectedState);
  assert.equal(finished.containers.length, 1);
  await refresh(page);
}

async function main() {
  assert.ok(['readonly', 'actions'].includes(PHASE));
  const anonymous = await request.newContext({ignoreHTTPSErrors: true});
  for (const url of PROTECTED_PATHS) {
    assert.equal((await anonymous.get(CONFIG.origin + url)).status(), 401);
  }
  assert.equal((await anonymous.post(CONFIG.origin + '/v1/actions', {data: {}})).status(), 401);
  await anonymous.dispose();
  const wrong = await request.newContext({ignoreHTTPSErrors: true,
    httpCredentials: {username: CONFIG.username, password: 'incorrect-demo-password', send: 'always'}});
  assert.equal((await wrong.get(CONFIG.origin + '/v1/status')).status(), 401);
  await wrong.dispose();
  const browser = await chromium.launch({headless: true});
  try {
    for (const viewport of VIEWPORTS) {
      const mobile = viewport.width < 720;
      const context = await browser.newContext({viewport, isMobile: mobile, hasTouch: mobile,
        ignoreHTTPSErrors: true,
        httpCredentials: {username: CONFIG.username, password: CONFIG.password, origin: CONFIG.origin}});
      const page = await context.newPage();
      page.setDefaultTimeout(TIMEOUT);
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await untilStatus(context, data => data.ready && data.containers.length === 1);
      const initial = await status(context);
      assert.equal(initial.containers[0].id, CONFIG.demo_id);
      assert.equal(initial.manual_actions.enabled, PHASE === 'actions');
      for (const url of PRIVATE_PATHS) {
        assert.equal((await context.request.get(CONFIG.origin + url)).status(), 404);
      }
      await page.goto(CONFIG.origin);
      await page.waitForFunction(() => document.getElementById('connection').textContent === 'Agent connected');
      assert.equal(await page.locator('.container-row').count(), 1);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      if (PHASE === 'readonly') {
        assert.equal(await page.locator('.container-row button:visible').count(), 0);
        assert.match(await page.locator('#mode').textContent(), /Read-only/);
        assert.equal((await context.request.post(CONFIG.origin + '/v1/actions', {data: {},
          headers: {Origin: CONFIG.origin}})).status(), 405);
      } else if (mobile) {
        const before = inspectDemo().StartedAt;
        await page.getByRole('button', {name: `Restart ${CONFIG.demo_name}`, exact: true}).click();
        await page.getByRole('button', {name: 'Cancel', exact: true}).click();
        assert.equal(inspectDemo().StartedAt, before);
        assert.equal((await status(context)).manual_actions.recent.length, 0);
        const rejected = await context.request.post(CONFIG.origin + '/v1/actions', {
          data: {request_id: 'f'.repeat(32), container_id: CONFIG.demo_id, action: 'restart'},
          headers: {Origin: 'https://wrong-origin.invalid'}});
        assert.equal(rejected.status(), 403);
        await perform(page, context, 'Stop', 'exited');
        await perform(page, context, 'Start', 'running');
        const started = inspectDemo().StartedAt;
        await perform(page, context, 'Restart', 'running');
        assert.notEqual(inspectDemo().StartedAt, started);
        assert.equal((await status(context)).manual_actions.recent.length, 3);
        assert.equal(await page.locator('#activity li[data-state="succeeded"]').count(), 3);
      }
      await page.screenshot({path: path.join(CONFIG.output, `${PHASE}-${mobile ? 'mobile' : 'desktop'}.png`), fullPage: true});
      assert.deepEqual(errors, []);
      await context.close();
    }
    console.log(`Published images: ${PHASE} desktop/mobile checks passed.`);
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
