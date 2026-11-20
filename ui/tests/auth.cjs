/* Browser authentication lifecycle with synthetic API responses; no Docker actions. */
'use strict';
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../static');
const server = http.createServer((req, res) => {
  const file = {'/': 'index.html', '/auth.js': 'auth.js', '/app.js': 'app.js', '/app.css': 'app.css'}[req.url];
  if (!file) return res.writeHead(404).end();
  res.setHeader('Content-Type', file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
  res.end(fs.readFileSync(path.join(root, file)));
});
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless: true, ...(process.env.CHROMIUM_EXECUTABLE ? {executablePath: process.env.CHROMIUM_EXECUTABLE} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}});
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    let signedIn = false, logouts = 0, mutations = 0, failLogout = true;
    const csrf = 'synthetic-csrf-token';
    await page.route('**/v1/auth/info', route => route.fulfill({json: {enabled: true, api_version: 1}}));
    await page.route('**/v1/auth/me', route => route.fulfill({status: signedIn ? 200 : 401, json: {username: 'reader', scopes: ['status:read']}}));
    await page.route('**/v1/auth/login', route => {
      const data = route.request().postDataJSON();
      assert.equal(data.username, 'reader'); assert.equal(data.password, 'test-only-password');
      signedIn = true;
      return route.fulfill({json: {username: 'reader', csrf_token: csrf}});
    });
    await page.route('**/v1/auth/logout', route => {
      logouts++;
      assert.equal(route.request().headers()['x-csrf-token'], csrf);
      assert.deepEqual(route.request().postDataJSON(), {});
      if (failLogout) return route.fulfill({status: 503, json: {error: 'authentication_unavailable'}});
      signedIn = false;
      return route.fulfill({json: {revoked: true}});
    });
    await page.route('**/v1/status', route => route.fulfill({status: signedIn ? 200 : 401, json: {
      api_version: 1, ready: true, containers: [], actions: {}, manual_actions: {enabled: false}
    }}));
    await page.route('**/v1/actions', route => { mutations++; return route.abort(); });
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.getByLabel('Username', {exact: true}).fill('reader');
    await page.getByLabel('Password', {exact: true}).fill('test-only-password');
    await page.getByRole('button', {name: 'Sign in', exact: true}).click();
    await page.getByRole('button', {name: 'Sign out'}).waitFor();
    assert.equal(await page.evaluate(() => sessionStorage.getItem('monit.csrf.v1')), csrf);
    assert.equal(await page.evaluate(() => JSON.stringify({...localStorage, ...sessionStorage})).then(x => x.includes('test-only-password')), false);
    await page.getByRole('button', {name: 'Sign out'}).click();
    await page.getByText('Sign-out could not be confirmed. Please retry.').waitFor();
    failLogout = false;
    await page.getByRole('button', {name: 'Sign out'}).click();
    await page.getByRole('button', {name: 'Sign in', exact: true}).waitFor();
    assert.equal(await page.evaluate(() => sessionStorage.getItem('monit.csrf.v1')), null);
    assert.equal(logouts, 2); assert.equal(mutations, 0); assert.deepEqual(errors, []);
    console.log('SQLite browser login, failed logout, revocation and mobile layout: OK');
  } finally { await browser.close(); server.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; server.close(); });
