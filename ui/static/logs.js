'use strict';
const $ = id => document.getElementById(id);
const ERRORS = {
  400: 'Invalid filters or cursor. Check the filters or choose Latest events.',
  401: 'Sign in to view the journal.', 403: 'Journal access was denied. Check the authenticated proxy configuration.',
  404: 'The private journal is not enabled on this agent.', 410: 'This view expired after rotation or a restart. Choose Latest events.',
  503: 'The journal is busy or unavailable. Try again shortly.'
};
const DETAIL_FIELDS = ['category', 'event', 'result', 'source', 'actor', 'host', 'event_id', 'correlation_id', 'container_id', 'command_id', 'rule_id', 'reason', 'error_code', 'exit_code', 'duration_ms', 'channel', 'notification_id', 'alert_name', 'alert_status', 'delivery_status'];
const ACTION_LABELS = {
  start: 'Start', stop: 'Stop', restart: 'Restart', pause: 'Pause', unpause: 'Resume',
  'maintenance-15m': 'Maintenance pause (15 minutes)',
  'maintenance-1h': 'Maintenance pause (1 hour)', 'maintenance-off': 'Maintenance resume',
  'restart-reset': 'Restart budget reset'
};
const FILTER_LABELS = {since: 'From', until: 'To', container: 'Container', source: 'Source', category: 'Category', result: 'Result'};
const RESULT_LABELS = {pending: 'Pending (queued or in progress)', succeeded: 'Succeeded', failed: 'Failed', rejected: 'Rejected', skipped: 'Skipped', simulated: 'Simulation', accepted: 'Accepted', received: 'Received'};
const ACTION_ERRORS = {...ERRORS, 400: 'Invalid action filter or cursor. Try Refresh action.', 410: 'This action view expired. Choose Refresh action.'};
function eventPresentation(record) {
  const outcome = record.result || record.event;
  const states = {
    failed: ['danger', 'Failed', 'failed'], rejected: ['danger', 'Rejected', 'rejected'],
    succeeded: ['success', 'Succeeded', 'completed'], skipped: ['warning', 'Skipped', 'skipped'],
    simulated: ['secondary', 'Simulation', 'simulated'],
    accepted: ['info', 'Accepted', 'accepted'], received: ['info', 'Received', 'received']
  };
  const pending = {
    queued: ['info', 'Queued', 'queued'], started: ['progress', 'In progress', 'started']
  };
  const state = (Object.hasOwn(states, outcome) && states[outcome]) || ((outcome === 'pending' || !record.result) && Object.hasOwn(pending, record.event) && pending[record.event])
    || ['secondary', outcome || 'Recorded', record.event || 'recorded'];
  const action = record.category === 'notification' ? 'Notification'
    : (Object.hasOwn(ACTION_LABELS, record.action) && ACTION_LABELS[record.action]) || record.action || 'Action';
  const target = record.container_name || record.container_id
    ? `Container: ${record.container_name || record.container_id}`
    : record.alert_name ? `Alert: ${record.alert_name}` : record.channel ? `Channel: ${record.channel}` : null;
  const source = record.source === 'manual' ? 'Manual action'
    : record.source === 'automatic' ? 'Automatic action' : `Source: ${record.source || 'unknown'}`;
  const actor = record.actor ? `Actor: ${record.actor}` : 'Actor: unknown';
  const sourceTone = record.source === 'manual' ? 'manual' : record.source === 'automatic' ? 'automatic' : 'neutral';
  return {tone: state[0], status: state[1],
    title: action + ' ' + state[2], target,
    source, sourceTone, actor, host: record.host && `Host: ${record.host}`};
}
let filters = new URLSearchParams();
let history = [null];
let pageIndex = 0;
let current = null;
let controller = null;
let generation = 0;
let loading = false;
let exporting = false;
const ACTION_EVENT_LIMIT = 500;
let actionView = null;
let actionController = null;
let actionGeneration = 0;

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function filterTag(className, text, key, value) {
  const field = $('log-filters').elements.namedItem(key);
  const supported = value && (field.tagName !== 'SELECT' || Array.from(field.options).some(option => option.value === value));
  const tag = node(supported ? 'button' : 'span', className, text);
  if (supported) {
    tag.type = 'button';
    tag.classList.add('log-filter-link');
    tag.title = `Filter by ${FILTER_LABELS[key].toLowerCase()}: ${key === 'result' ? RESULT_LABELS[value] : value}`;
    tag.setAttribute('aria-label', tag.title);
    tag.setAttribute('aria-pressed', String(filters.get(key) === value));
    tag.addEventListener('click', () => {
      const next = new URLSearchParams(filters);
      next.set(key, value);
      setFilters(next);
      $('active-log-filters').querySelector(`[data-filter="${key}"]`).focus();
    });
  }
  return tag;
}
function setFilters(next) {
  filters = next;
  const active = $('active-log-filters');
  active.replaceChildren();
  for (const [key, label] of Object.entries(FILTER_LABELS)) {
    const value = filters.get(key) || '';
    const field = $('log-filters').elements.namedItem(key);
    const date = value && (key === 'since' || key === 'until') ? new Date(value) : null;
    field.value = date ? new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16) : value;
    if (!value) continue;
    const display = date ? date.toLocaleString() : key === 'result' ? RESULT_LABELS[value] || value : value;
    const remove = node('button', 'log-tag', `${label}: ${display} ×`);
    remove.type = 'button';
    remove.dataset.filter = key;
    remove.setAttribute('aria-label', `Remove ${label.toLowerCase()} filter: ${display}`);
    remove.addEventListener('click', () => {
      const remaining = new URLSearchParams(filters);
      remaining.delete(key);
      setFilters(remaining);
      (active.querySelector('button') || $('log-filters').querySelector('[type="submit"]')).focus();
    });
    active.append(remove);
  }
  active.hidden = filters.size === 0;
  load(null, 0, true);
}
function controls() {
  $('previous-logs').disabled = loading || exporting || pageIndex === 0;
  $('older-logs').disabled = loading || exporting || !current?.next_cursor;
  for (const id of ['export-jsonl', 'export-csv']) $(id).disabled = loading || exporting || !current?.records.length;
}
function eventRow(record, interactive = true) {
  const presentation = eventPresentation(record);
  const row = node('li', 'log-event');
  row.dataset.tone = presentation.tone;
  const heading = node('div', 'log-event-heading');
  const stamp = node('time', '', new Date(record.timestamp).toLocaleString());
  stamp.dateTime = record.timestamp;
  const result = interactive ? filterTag('badge', presentation.status, 'result', record.result) : node('span', 'badge', presentation.status);
  result.dataset.result = record.result || '';
  const title = node('h3', '', presentation.title);
  heading.append(title);
  if (presentation.host) {
    const host = node('span', 'log-heading-part');
    host.append(node('span', 'log-separator', '·'), node('span', 'log-tag log-host', presentation.host));
    heading.append(host);
  }
  const date = node('span', 'log-heading-part log-date');
  date.append(node('span', 'log-separator', '·'), stamp);
  heading.append(date);
  const summary = node('p', 'log-meta');
  const source = interactive ? filterTag('log-tag', presentation.source, 'source', record.source) : node('span', 'log-tag', presentation.source);
  source.dataset.kind = presentation.sourceTone;
  if (presentation.target) summary.append(interactive ? filterTag('log-tag log-target', presentation.target, 'container', record.container_id || record.container_name) : node('span', 'log-tag log-target', presentation.target));
  summary.append(source, node('span', 'log-tag', presentation.actor), result);
  const details = node('details');
  details.append(node('summary', '', 'Event details'));
  const values = node('dl');
  for (const key of DETAIL_FIELDS) {
    if (record[key] === null || record[key] === undefined) continue;
    values.append(node('dt', '', key.replaceAll('_', ' ')), node('dd', '', String(record[key])));
  }
  details.append(values);
  row.append(heading, summary);
  if (record.reason !== null && record.reason !== undefined && record.reason !== '') {
    const reason = node('p', 'log-reason');
    reason.append(node('strong', '', 'Reason: '), node('code', '', String(record.reason)));
    row.append(reason);
  }
  if (interactive && record.category === 'action' && typeof record.correlation_id === 'string' && record.correlation_id.length > 0 && record.correlation_id.length <= 256) {
    const view = node('button', 'log-tag log-view-action', 'View action');
    view.type = 'button';
    view.addEventListener('click', () => openAction(record.correlation_id));
    summary.append(view);
  }
  if (!interactive && Number.isFinite(record.duration_ms) && record.duration_ms >= 0) {
    row.append(node('p', 'log-reason', `Duration: ${record.duration_ms} ms`));
  }
  row.append(details);
  return row;
}
function render() {
  const fragment = document.createDocumentFragment();
  for (const record of current.records) fragment.append(eventRow(record));
  $('log-events').replaceChildren(fragment);
  $('log-count').textContent = String(current.records.length);
  $('log-empty').hidden = current.records.length !== 0;
  $('log-page').textContent = `Page ${pageIndex + 1}`;
  $('log-notice').textContent = current.next_cursor
    ? current.records.length ? 'More history is available. Choose Older events to continue.' : 'No matches in this part of the journal. Choose Older events to continue searching.'
    : current.records.length ? 'End of retained history for these filters.' : 'No matching events in the remaining history.';
}
async function load(cursor, index, reset = false) {
  controller?.abort();
  controller = new AbortController();
  const pending = controller;
  const mine = ++generation;
  loading = true; current = null; controls();
  $('log-events').replaceChildren(); $('log-count').textContent = '0'; $('log-empty').hidden = true;
  $('log-notice').dataset.error = 'false';
  $('log-notice').textContent = 'Loading events…';
  const params = new URLSearchParams(filters);
  if (cursor) params.set('cursor', cursor);
  const timeout = setTimeout(() => pending.abort(), 10000);
  try {
    const response = await fetch('/v1/audit?' + params, {cache: 'no-store', credentials: 'same-origin', signal: pending.signal});
    if (!response.ok) throw new Error(ERRORS[response.status] || 'Unable to load events. Check the filters and try again.');
    const result = await response.json();
    if (mine !== generation) return;
    current = result; pageIndex = index;
    if (reset) history = [result.page_cursor];
    else history[index] = result.page_cursor;
    render();
  } catch (error) {
    if (mine === generation) {
      $('log-notice').dataset.error = 'true';
      $('log-notice').textContent = error.name === 'AbortError' ? 'The request timed out. Try again.' : error.message;
    }
  } finally {
    clearTimeout(timeout);
    if (mine === generation) { loading = false; controls(); }
  }
}
function applyFilters() {
  const next = new URLSearchParams();
  for (const [key, value] of new FormData($('log-filters'))) {
    if (!value) continue;
    next.set(key, key === 'since' || key === 'until' ? new Date(value).toISOString() : value);
  }
  setFilters(next);
}
function openAction(id) {
  actionView = {id, records: [], next: null, loading: false};
  $('action-correlation').textContent = id;
  $('action-dialog').showModal();
  loadAction(true, current.records.filter(record => record.category === 'action' && record.correlation_id === id));
}
function actionControls() {
  $('refresh-action').disabled = !actionView || actionView.loading;
  $('older-action').disabled = !actionView || actionView.loading || !actionView.next || actionView.records.length >= ACTION_EVENT_LIMIT;
}
async function loadAction(reset = false, preview = []) {
  if (!actionView || (actionView.loading && !reset)) return;
  actionController?.abort();
  actionController = new AbortController();
  const pending = actionController;
  const mine = ++actionGeneration;
  const view = actionView;
  if (reset) {
    view.records = preview.slice(0, ACTION_EVENT_LIMIT);
    view.next = null;
    $('action-events').replaceChildren(...view.records.slice().reverse().map(record => eventRow(record, false)));
  }
  view.loading = true;
  actionControls();
  $('action-notice').dataset.error = 'false';
  $('action-notice').textContent = reset && view.records.length
    ? 'Loading action events… Showing previously loaded events while checking retained history.' : 'Loading action events…';
  const params = new URLSearchParams({category: 'action', correlation_id: view.id});
  if (!reset && view.next) params.set('cursor', view.next);
  const timeout = setTimeout(() => pending.abort(), 10000);
  try {
    const response = await fetch('/v1/audit?' + params, {cache: 'no-store', credentials: 'same-origin', signal: pending.signal});
    if (!response.ok) throw new Error(ACTION_ERRORS[response.status] || 'Unable to load action events. Try Refresh action.');
    const result = await response.json();
    if (mine !== actionGeneration) return;
    if (reset) view.records = [];
    const remaining = ACTION_EVENT_LIMIT - view.records.length;
    const truncated = result.records.length > remaining;
    view.records.push(...result.records.slice(0, remaining));
    view.next = result.next_cursor;
    $('action-events').replaceChildren(...view.records.slice().reverse().map(record => eventRow(record, false)));
    $('action-notice').textContent = view.next || truncated
      ? view.records.length >= ACTION_EVENT_LIMIT ? 'Display limit reached (500 events). Earlier events may exist.' : `${view.records.length} action events loaded. Choose Load older events to search earlier history.`
      : view.records.length ? `${view.records.length} action events loaded. End of retained history for this snapshot.` : 'No matching action events remain in retained history.';
  } catch (error) {
    if (mine === actionGeneration) {
      $('action-notice').dataset.error = 'true';
      $('action-notice').textContent = error.name === 'AbortError' ? 'The request timed out. Try Refresh action.' : error.message;
      if (reset && view.records.length) $('action-notice').textContent += ' Displayed events are previously loaded and may be incomplete or outdated.';
    }
  } finally {
    clearTimeout(timeout);
    if (mine === actionGeneration) { view.loading = false; actionControls(); }
  }
}
$('close-action').addEventListener('click', () => $('action-dialog').close());
$('action-dialog').addEventListener('close', () => {
  if ($('action-dialog').open) return;
  ++actionGeneration;
  actionController?.abort();
  actionView = null;
  $('action-events').replaceChildren();
});
$('refresh-action').addEventListener('click', () => loadAction(true, actionView?.records || []));
$('older-action').addEventListener('click', () => loadAction());
async function download(format) {
  if (!current || loading || exporting) return;
  const params = new URLSearchParams(filters);
  params.set('cursor', current.page_cursor); params.set('format', format);
  exporting = true; controls();
  const abort = new AbortController();
  const timeout = setTimeout(() => abort.abort(), 10000);
  try {
    const response = await fetch('/v1/audit/export?' + params, {cache: 'no-store', credentials: 'same-origin', signal: abort.signal});
    if (!response.ok) throw new Error(ERRORS[response.status] || 'Export failed. Refresh the journal and try again.');
    const url = URL.createObjectURL(await response.blob());
    const link = node('a'); link.href = url; link.download = `monit-docker-events.${format}`;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { $('log-notice').dataset.error = 'true'; $('log-notice').textContent = error.name === 'AbortError' ? 'Export timed out. Try again.' : error.message; }
  finally { clearTimeout(timeout); exporting = false; controls(); }
}
$('log-filters').addEventListener('submit', event => { event.preventDefault(); applyFilters(); });
$('reset-logs').addEventListener('click', () => { $('log-filters').reset(); applyFilters(); });
$('refresh-logs').addEventListener('click', applyFilters);
$('older-logs').addEventListener('click', () => { if (current?.next_cursor) load(current.next_cursor, pageIndex + 1); });
$('previous-logs').addEventListener('click', () => { if (pageIndex) load(history[pageIndex - 1], pageIndex - 1); });
$('export-jsonl').addEventListener('click', () => download('jsonl'));
$('export-csv').addEventListener('click', () => download('csv'));
applyFilters();
