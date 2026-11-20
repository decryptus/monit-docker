/* Synthetic API browser acceptance; no paid provider or Docker access. */
'use strict';
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const root = path.resolve(__dirname, '../static');
const server = http.createServer((req, res) => {
  const file = {'/ai-i18n.js': 'ai-i18n.js', '/vendor/i18next.min.js': 'vendor/i18next.min.js', '/locales/ai-en.json': 'locales/ai-en.json', '/locales/ai-fr.json': 'locales/ai-fr.json', '/logo.svg': 'logo.svg', '/': 'index.html', '/auth.js': 'auth.js', '/app.js': 'app.js', '/app.css': 'app.css'}[req.url];
  if (!file) return res.writeHead(404).end();
  res.setHeader('Content-Type', file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.json') ? 'application/json' : file.endsWith('.svg') ? 'image/svg+xml' : 'text/html');
  res.end(fs.readFileSync(path.join(root, file)));
});
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true, ...(process.env.CHROMIUM_EXECUTABLE ? {executablePath: process.env.CHROMIUM_EXECUTABLE} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let language = 'en';
    let enabled = false, previews = 0, calls = 0, actions = 0, fail = null;
    const id = 'a'.repeat(64);
    const context = {freshness: 'current', evidence: [{id: 'e1', field: 'mem_percent', value: 96}], missing_evidence: []};
    await page.route('**/v1/status', route => route.fulfill({json: {api_version: 1, ai_enabled: enabled, ready: true,
      containers: [{id, name: 'web', status: 'running', mem_percent: 96}], manual_actions: {enabled: false}, actions: {}}}));
    await page.route('**/v1/actions', route => { actions++; return route.abort(); });
    await page.route('**/v1/ai/preview', route => {
      previews++;
      assert.deepEqual(route.request().postDataJSON(), {container_id: id, language});
      return route.fulfill({json: {preview_id: 'b'.repeat(64), language, context, expires_in: 60}});
    });
    await page.route('**/v1/ai/explain', route => {
      calls++;
      assert.deepEqual(route.request().postDataJSON(), {container_id: id, preview_id: 'b'.repeat(64), language});
      if (fail) return route.fulfill({status: fail === 'evidence_changed' ? 409 : 504, json: {error: fail}});
      return route.fulfill({json: {elapsed_ms: 15, usage: {}, analysis: {
        hypotheses: [{text: '<img src=x onerror=alert(1)> Possible pressure', evidence: ['e1']}],
        missing_evidence: ['No recent memory trend.'], suggested_checks: [{text: 'Inspect memory history.', evidence: ['e1']}]
      }}});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.waitForFunction(() => document.getElementById('collection').textContent === 'Healthy');
    await page.locator('#auto').uncheck();
    assert.equal(await page.locator('.ai-open').isVisible(), false);
    enabled = true;
    await page.locator('#refresh').click();
    await page.locator('.ai-open').click();
    await page.waitForFunction(() => !document.getElementById('ai-explain').disabled);
    assert.equal(previews, 1); assert.equal(calls, 0);
    language = 'fr';
    await page.locator('#ai-language').selectOption('fr');
    await page.waitForFunction(() => !document.getElementById('ai-explain').disabled);
    assert.equal(previews, 2); assert.equal(calls, 0);
    assert.match(await page.locator('#ai-state').textContent(), /Français/);
    language = 'en';
    await page.locator('#ai-language').selectOption('en');
    await page.waitForFunction(() => !document.getElementById('ai-explain').disabled);
    assert.match(await page.locator('#ai-context').textContent(), /mem_percent/);
    await page.locator('#ai-explain').click();
    await page.waitForFunction(() => document.querySelectorAll('#ai-results section').length === 3);
    assert.equal(calls, 1); assert.equal(actions, 0);
    assert.equal(await page.locator('#ai-results img').count(), 0);
    assert.match(await page.locator('#ai-results').textContent(), /<img/);
    assert.equal(await page.locator('#ai-explain').isDisabled(), true);
    for (const width of [360, 390, 720, 1280]) {
      await page.setViewportSize({width, height: 844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflow at ${width}`);
      assert.equal(await page.locator('#ai-close').isVisible(), true);
    }
    for (const error of ['evidence_changed', 'provider_timeout']) {
      fail = error;
      await page.locator('#ai-preview').click();
      await page.waitForFunction(() => !document.getElementById('ai-explain').disabled);
      await page.locator('#ai-explain').click();
      await page.waitForFunction(() => !document.getElementById('ai-preview').disabled);
      assert.equal(await page.locator('#ai-explain').isDisabled(), true);
      assert.match(await page.locator('#ai-state').textContent(), error === 'evidence_changed' ? /Observations changed/ : /timed out/);
      await page.locator('#ai-close').click();
      await page.locator('#refresh').click();
      await page.waitForFunction(() => document.getElementById('collection').textContent === 'Healthy');
      assert.equal(actions, 0);
      await page.locator('.ai-open').click();
      await page.waitForFunction(() => !document.getElementById('ai-explain').disabled);
    }
    assert.equal(calls, 3); assert.deepEqual(errors, []);
    console.log('AI browser acceptance passed: explicit consent, escaped text, errors, no retries/actions, desktop/mobile.');
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; }).finally(() => server.close());
