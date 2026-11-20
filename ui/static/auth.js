'use strict';

// The session credential stays in a Secure HttpOnly cookie. Only the CSRF
// token lives in this tab's sessionStorage; passwords are never persisted.
window.MonitAuth = (() => {
  const KEY = 'monit.csrf.v1';
  const rawFetch = window.fetch.bind(window);
  let enabled = false;
  let unavailable = false;
  let csrf = sessionStorage.getItem(KEY);
  let dialog;
  const element = (tag, text) => {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    return node;
  };
  async function authFetch(path, options = {}) {
    return rawFetch('/v1/auth/' + path, {...options, credentials: 'same-origin',
      cache: 'no-store', signal: AbortSignal.timeout(10000)});
  }
  function clear() { csrf = null; sessionStorage.removeItem(KEY); }
  function login() {
    if (dialog) { if (!dialog.open) dialog.showModal(); return; }
    dialog = element('dialog');
    dialog.id = 'auth-dialog';
    const title = element('h2', 'Sign in to monit-docker');
    title.id = 'auth-title';
    dialog.setAttribute('aria-labelledby', title.id);
    dialog.addEventListener('cancel', event => event.preventDefault());
    const form = element('form');
    const userLabel = element('label', 'Username');
    const user = element('input');
    user.name = 'username'; user.autocomplete = 'username'; user.required = true; user.maxLength = 64;
    userLabel.append(user);
    const passwordLabel = element('label', 'Password');
    const password = element('input');
    password.name = 'password'; password.type = 'password'; password.autocomplete = 'current-password'; password.required = true;
    passwordLabel.append(password);
    const error = element('p'); error.setAttribute('role', 'alert');
    const submit = element('button', 'Sign in'); submit.type = 'submit'; submit.className = 'primary';
    form.append(title, userLabel, passwordLabel, error, submit);
    form.addEventListener('submit', async event => {
      event.preventDefault(); submit.disabled = true; error.textContent = '';
      try {
        const response = await authFetch('login', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({username: user.value, password: password.value})});
        password.value = '';
        if (!response.ok) throw new Error(response.status === 401 ? 'Sign-in failed. Check your credentials or try again later.' : 'Authentication is unavailable.');
        const data = await response.json();
        if (typeof data.csrf_token !== 'string') throw new Error('Invalid authentication response.');
        sessionStorage.setItem(KEY, data.csrf_token);
        location.reload();
      } catch (reason) { password.value = ''; error.textContent = reason.message; }
      finally { submit.disabled = false; }
    });
    dialog.append(form); document.body.append(dialog); dialog.showModal();
  }
  function account(identity) {
    const container = element('div'); container.className = 'auth-account';
    const name = element('span', identity.username);
    const logout = element('button', 'Sign out');
    const message = element('span'); message.setAttribute('role', 'alert');
    logout.addEventListener('click', async () => {
      logout.disabled = true;
      try {
        const response = await authFetch('logout', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: '{}'});
        if (!response.ok) throw new Error('Sign-out could not be confirmed. Please retry.');
        clear(); location.reload();
      } catch (error) { message.textContent = error.message; logout.disabled = false; }
    });
    container.append(name, logout, message); document.querySelector('.topbar').append(container);
  }
  const ready = (async () => {
    try {
      const info = await authFetch('info');
      if (info.status === 404) return; // Compatibility with the explicit legacy proxy configuration.
      if (!info.ok) throw new Error('Authentication discovery failed');
      enabled = (await info.json()).enabled === true;
      if (!enabled) return;
      if (!csrf) { login(); return; }
      const response = await authFetch('me');
      if (response.status === 401) { clear(); login(); return; }
      if (!response.ok) throw new Error('Authentication unavailable');
      account(await response.json());
    } catch (_) { unavailable = true; }
  })();
  return {ready, async fetch(url, options = {}) {
    await ready;
    const target = new URL(url, location.href);
    if (target.origin !== location.origin) throw new Error('Cross-origin API requests are disabled.');
    if (unavailable) throw new Error('Authentication is unavailable. Reload to retry.');
    if (enabled && !csrf) { login(); throw new Error('Sign in to continue.'); }
    const headers = new Headers(options.headers);
    if (enabled && !['GET', 'HEAD'].includes((options.method || 'GET').toUpperCase())) headers.set('X-CSRF-Token', csrf);
    const response = await rawFetch(url, {...options, headers, credentials: 'same-origin'});
    if (enabled && response.status === 401) { clear(); login(); }
    return response;
  }};
})();
