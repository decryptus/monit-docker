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
function render() {
  const fragment = document.createDocumentFragment();
  for (const record of current.records) {
    const presentation = eventPresentation(record);
    const row = node('li', 'log-event');
    row.dataset.tone = presentation.tone;
    const heading = node('div', 'log-event-heading');
    const stamp = node('time', '', new Date(record.timestamp).toLocaleString());
    stamp.dateTime = record.timestamp;
    const result = filterTag('badge', presentation.status, 'result', record.result);
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
    const source = filterTag('log-tag', presentation.source, 'source', record.source);
    source.dataset.kind = presentation.sourceTone;
    if (presentation.target) summary.append(filterTag('log-tag log-target', presentation.target, 'container', record.container_id || record.container_name));
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
    row.append(details);
    fragment.append(row);
  }
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
