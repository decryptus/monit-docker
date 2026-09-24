'use strict';

// No external resources, HTML interpolation, credentials or Docker access.
const $ = id => document.getElementById(id);
const rows = new Map();
const labels = {start: 'Start', stop: 'Stop', restart: 'Restart', 'restart-reset': 'Rearm auto restarts', 'maintenance-15m': 'Maintenance 15 min', 'maintenance-1h': 'Maintenance 1 h', 'maintenance-off': 'Resume auto actions'};
const HEALTH_LABELS = {
  healthy: 'Health: healthy', unhealthy: 'Health: unhealthy',
  starting: 'Health: starting', none: 'No healthcheck', unknown: 'Health: unknown'
};
const reasons = {
  busy: 'Another action is queued or running.', not_ready: 'Fresh data is required.',
  not_selected: 'The container is no longer selected.', state_changed: 'The container state changed.',
  container_protected: 'Manual actions are blocked for this container. Automatic rules remain active.',
  cooldown: 'The manual action cooldown has not elapsed.', expired: 'The queued request expired.',
  no_restart_attempts: 'There are no automatic restart attempts to reset.',
  execution_failed: 'Execution failed; inspect the agent logs.',
  forbidden: 'The proxy action secret was rejected.', origin_rejected: 'The browser origin was rejected.',
  invalid_request: 'The action request was invalid.', request_id_conflict: 'The request ID was already used.'
};
let data = null;
let connected = false;
let loading = false;
let posting = false;
let selected = null;
let unresolved = null;
let timer = null;
let message = '';
let receivedAt = 0;

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function number(value) { return Number.isFinite(value) ? value.toLocaleString() : '—'; }
function percent(value) { return Number.isFinite(value) ? `${value.toFixed(1)}%` : '—'; }
function bytes(value) {
  if (!Number.isFinite(value)) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
  return `${value.toFixed(unit ? 1 : 0)} ${units[unit]}`;
}
function date(value) {
  return Number.isFinite(value) ? new Date(value * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'}) : '—';
}
function actions() { return data?.manual_actions || {enabled: false}; }
function busy() { return (actions().recent || []).some(item => ['queued', 'running'].includes(item.status)); }
function canAct(container, action) {
  return connected && performance.now() - receivedAt <= 10000 && data?.ready
    && actions().enabled && !container.manual_actions_protected && !posting && !unresolved && !busy()
    && (action !== 'maintenance-off' || container.maintenance_active)
    && (action !== 'restart-reset' || hasRestartBudget(container))
    && (actions().allowed_states?.[action] || []).includes(container.status);
}
function hasRestartBudget(container) {
  return Number.isInteger(container.restart_limit) && container.restart_limit > 0
    && Number.isInteger(container.restart_attempts) && container.restart_attempts > 0;
}
function runtimeSummary(container) {
  const parts = [];
  if (container.maintenance_active) parts.push(`Maintenance until ${date(container.maintenance_until)} · automatic actions paused`);
  if (Number.isFinite(container.pids_current)) {
    parts.push(`PIDs: ${number(container.pids_current)}${Number.isFinite(container.pids_limit) ? '/' + number(container.pids_limit) + ' · ' + percent(container.pids_percent) : ' · no configured limit'}`);
  }
  if (Number.isFinite(container.event_window_seconds)) {
    parts.push(container.event_history_complete === 0 ? 'Event history incomplete' :
      `Last ${number(container.event_window_seconds)}s: ${number(container.oom_events)} OOM · ${number(container.starts_recent)} starts`);
  }
  return parts.join(' | ');
}
function newRow(container) {
  const article = node('article', 'container-row');
  const identity = node('div', 'identity');
  const name = node('h3');
  const id = node('span', 'identifier');
  identity.append(name, id);
  const statusCell = node('div', 'status-cell');
  const status = node('span', 'badge');
  const health = node('span', 'metric-detail health-state');
  const restarts = node('span', 'metric-detail restart-budget');
  const runtime = node('span', 'metric-detail runtime-checks');
  const protection = node('span', 'protection');
  const lock = node('span', 'protection-lock');
  lock.setAttribute('aria-hidden', 'true');
  protection.append(lock, node('span', '', 'Protected'));
  protection.title = reasons.container_protected;
  statusCell.append(status, protection, health, restarts, runtime);
  const cpu = node('div', 'metric');
  const cpuValue = node('span', 'metric-value');
  cpu.append(node('span', 'cell-label', 'CPU'), cpuValue, node('span', 'metric-detail', '100% = one CPU core'));
  const memory = node('div', 'metric');
  const memoryValue = node('span', 'metric-value');
  const memoryDetail = node('span', 'metric-detail');
  memory.append(node('span', 'cell-label', 'Memory'), memoryValue, memoryDetail);
  const controls = node('div', 'controls');
  const buttons = {};
  for (const action of Object.keys(labels)) {
    const button = node('button', '', labels[action]);
    button.type = 'button'; button.dataset.action = action;
    button.addEventListener('click', () => confirmAction(container.id, action));
    buttons[action] = button; controls.append(button);
  }
  const readonly = node('span', 'read-only', 'Read only');
  controls.append(readonly);
  article.append(identity, statusCell, cpu, memory, controls);
  $('containers').append(article);
  const row = {article, name, id, status, health, restarts, runtime, protection, cpuValue, memoryValue, memoryDetail, buttons, readonly};
  rows.set(container.id, row);
  return row;
}
function renderContainers() {
  const items = connected && data?.ready ? data.containers : [];
  const ids = new Set(items.map(item => item.id));
  for (const [id, row] of rows) {
    if (!ids.has(id)) { row.article.remove(); rows.delete(id); }
  }
  const query = $('search').value.trim().toLowerCase();
  let shown = 0;
  for (const container of items) {
    const row = rows.get(container.id) || newRow(container);
    row.name.textContent = container.name;
    row.id.textContent = container.id.slice(0, 12);
    row.id.title = container.id;
    row.status.textContent = container.status;
    row.status.dataset.state = container.status;
    row.health.textContent = HEALTH_LABELS[container.health] || HEALTH_LABELS.unknown;
    row.health.dataset.health = Object.hasOwn(HEALTH_LABELS, container.health) ? container.health : 'unknown';
    row.restarts.hidden = !Number.isInteger(container.restart_limit);
    row.restarts.textContent = row.restarts.hidden ? '' :
      `Auto restarts: ${number(container.restart_attempts)}/${number(container.restart_limit)}${container.restart_attempts >= container.restart_limit ? ' · blocked' : ''}`;
    row.runtime.textContent = runtimeSummary(container);
    row.runtime.hidden = !row.runtime.textContent;
    row.runtime.title = 'PIDs include threads. Starts include the initial start, manual actions and Docker restart policies. Event history is limited to the current Docker daemon buffer.';
    row.protection.hidden = !container.manual_actions_protected;
    row.cpuValue.textContent = percent(container.cpu_percent);
    row.memoryValue.textContent = bytes(container.mem_usage);
    row.memoryDetail.textContent = `${percent(container.mem_percent)} · limit ${bytes(container.mem_limit)}`;
    row.readonly.hidden = actions().enabled;
    for (const [action, button] of Object.entries(row.buttons)) {
      button.hidden = !actions().enabled || !(actions().allowed_states?.[action] || []).includes(container.status)
        || (action === 'maintenance-off' && !container.maintenance_active)
        || (action === 'restart-reset' && !hasRestartBudget(container));
      button.disabled = !canAct(container, action);
      button.title = container.manual_actions_protected ? reasons.container_protected : '';
      button.setAttribute('aria-label', `${labels[action]} ${container.name}`);
    }
    row.article.hidden = !`${container.name} ${container.id} ${container.status}`.toLowerCase().includes(query);
    if (!row.article.hidden) shown++;
  }
  $('shown').textContent = String(shown);
  $('empty').hidden = shown > 0;
  $('empty').textContent = !connected ? 'Measurements unavailable until the connection returns.'
    : !data.ready ? 'No fresh measurements. Waiting for a successful collection.'
    : items.length ? 'No containers match your filter.' : 'No containers in the latest snapshot.';
}
function render() {
  $('journal-link').hidden = !connected || !data?.audit_enabled;
  const ready = connected && data?.ready;
  const items = ready ? data.containers : [];
  $('connection').textContent = !connected ? 'Disconnected' : ready ? 'Agent connected' : 'Agent not ready';
  $('connection').dataset.state = !connected ? 'offline' : ready ? 'live' : 'waiting';
  $('total').textContent = ready ? number(items.length) : '—';
  $('container-summary').textContent = ready ? `${items.filter(item => item.status === 'running').length} running · ${items.filter(item => item.status !== 'running').length} other` : 'Fresh measurements unavailable';
  $('collection').textContent = !connected ? 'Offline' : data.running ? 'Collecting' : ready ? 'Healthy' : 'Not ready';
  $('collected-at').textContent = data?.last_success_at ? `Last success at ${date(data.last_success_at)}` : 'No successful collection yet';
  $('cycles').textContent = connected ? number(data.cycles_total) : '—';
  $('errors').textContent = connected ? `${number(data.errors_total)} failed · since agent startup` : 'Agent unavailable';
  $('executed').textContent = connected ? number(data.actions?.executed) : '—';
  $('rule-summary').textContent = connected ? `${number(data.actions?.pending)} pending · ${number(data.actions?.cooldown)} cooldown · ${number(data.actions?.maintenance ?? 0)} maintenance · ${number(data.actions?.['restart-limit'])} restart limit · ${number(data.actions?.['dry-run'])} dry run` : 'Counters unavailable';
  $('mode').textContent = actions().enabled ? 'Manual controls enabled · one request at a time · automatic actions pause during maintenance.' : 'Read-only access. No action can be triggered from this page.';
  let notice = message;
  if (!connected) notice = 'Connection lost. Measurements and controls are unavailable. Reconnecting does not repeat an action.';
  else if (!ready) notice = data.last_error_code ? `Collection failed (code ${data.last_error_code}). Old measurements are hidden.` : 'Waiting for fresh data. The agent may be starting, collecting, or refreshing after an action.';
  if (unresolved) notice = 'Action receipt is unconfirmed. Controls are paused: refresh to check recent requests before trying anything else.';
  $('notice').textContent = notice;
  $('notice').hidden = !notice;
  $('notice').dataset.tone = connected && ready && !unresolved ? 'good' : 'warning';
  renderContainers();
  document.querySelector('.activity').hidden = !actions().enabled;
  const recent = actions().recent || [];
  const list = $('activity'); list.replaceChildren();
  for (const request of [...recent].reverse()) {
    const item = node('li'); item.dataset.state = request.status;
    const container = (data?.containers || []).find(c => c.id === request.container_id);
    item.append(node('span', '', `${labels[request.action] || request.action} · ${container?.name || request.container_id.slice(0, 12)}`),
      node('span', '', `${request.status} · ${date(request.finished_at || request.submitted_at)}${request.error ? ' · ' + (reasons[request.error] || request.error) : ''}${request.error_code ? ' (code ' + request.error_code + ')' : ''}`));
    list.append(item);
  }
  $('no-activity').hidden = recent.length > 0;
  if (selected) {
    const current = (data?.containers || []).find(item => item.id === selected.container.id);
    if (!current || !canAct(current, selected.action)) $('confirm-action').disabled = true;
  }
}
async function request(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(url, {...options, credentials: 'same-origin', cache: 'no-store', signal: controller.signal});
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const error = new Error(body.error || `HTTP ${response.status}`);
      error.rejected = [400, 401, 403, 404, 405, 409, 413, 415].includes(response.status);
      throw error;
    }
    return await response.json();
  } finally { clearTimeout(timeout); }
}
function schedule() {
  clearTimeout(timer);
  if ($('auto').checked && !document.hidden) timer = setTimeout(refresh, 5000);
}
async function refresh() {
  if (loading) return;
  loading = true; $('refresh').disabled = true;
  try {
    const next = await request('/v1/status');
    if (next.api_version !== 1 || !Array.isArray(next.containers)) throw new Error('Unsupported API');
    data = next; connected = true; receivedAt = performance.now();
    if (unresolved && (actions().recent || []).some(item => item.request_id === unresolved)) unresolved = null;
  } catch (_) { connected = false; }
  finally { loading = false; $('refresh').disabled = false; render(); schedule(); }
}
function confirmAction(id, action) {
  const container = data?.containers.find(item => item.id === id);
  if (!container || !canAct(container, action)) return;
  selected = {container, action};
  const rearm = action === 'restart-reset';
  $('confirm-title').textContent = rearm ? 'Rearm automatic restarts?' : `${labels[action]} container?`;
  $('confirm-description').textContent = `${container.name} (${container.id.slice(0, 12)})`;
  $('confirm-note').textContent = action.startsWith('maintenance-')
    ? (action === 'maintenance-off' ? 'Resume automatic rule actions on the next cycle, subject to existing cooldowns and restart limits.'
      : 'Pause this agent’s automatic rule actions for this container. Monitoring, manual controls and external notifications continue. Docker restart policies and other agents are unaffected. The pause expires automatically.')
    : rearm
    ? `Reset ${number(container.restart_attempts)} recorded attempts to zero. This does not restart the container itself; matching monitoring rules may restart it on the next cycle. Existing cooldowns remain in effect.`
    : 'Stopping or restarting interrupts the service. Configured monitoring rules may subsequently change its state again.';
  $('confirm-action').textContent = labels[action]; $('confirm-action').disabled = false;
  $('confirm').returnValue = 'cancel'; $('confirm').showModal();
}
$('confirm').addEventListener('close', async () => {
  const target = selected; selected = null;
  if ($('confirm').returnValue !== 'confirm' || !target) return;
  const current = data?.containers.find(item => item.id === target.container.id);
  if (!current || !canAct(current, target.action)) return;
  const requestId = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
  posting = true; message = ''; render();
  try {
    await request('/v1/actions', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({request_id: requestId, container_id: current.id, action: target.action})});
    // Keep controls disabled until the accepted record is observed in status.
    unresolved = requestId;
    message = `${labels[target.action]} request accepted. Follow its result in Manual activity.`;
  } catch (error) {
    if (!error.rejected) unresolved = requestId;
    message = reasons[error.message] || `Request rejected: ${error.message}`;
  } finally { posting = false; render(); await refresh(); }
});
$('search').addEventListener('input', renderContainers);
$('refresh').addEventListener('click', refresh);
$('auto').addEventListener('change', () => { schedule(); if ($('auto').checked) refresh(); });
document.addEventListener('visibilitychange', () => {
  clearTimeout(timer);
  if (!document.hidden) { connected = false; render(); refresh(); }
});
// Even a paused refresh or suspended request must not leave old controls live.
setInterval(() => {
  if (connected && performance.now() - receivedAt > 10000) {
    connected = false; render();
  }
}, 1000);
refresh();
