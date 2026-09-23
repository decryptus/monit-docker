'use strict';
const $ = id => document.getElementById(id);
const ERRORS = {
  400: 'Invalid filters or cursor. Check the filters or choose Latest events.',
  401: 'Sign in to view the journal.', 403: 'Journal access was denied. Check the authenticated proxy configuration.',
  404: 'The private journal is not enabled on this agent.', 410: 'This view expired after rotation or a restart. Choose Latest events.',
  503: 'The journal is busy or unavailable. Try again shortly.'
};
const DETAIL_FIELDS = ['event_id', 'correlation_id', 'container_id', 'command_id', 'rule_id', 'reason', 'error_code', 'exit_code', 'duration_ms', 'channel', 'notification_id', 'alert_name', 'alert_status', 'delivery_status'];
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
function controls() {
  $('previous-logs').disabled = loading || exporting || pageIndex === 0;
  $('older-logs').disabled = loading || exporting || !current?.next_cursor;
  for (const id of ['export-jsonl', 'export-csv']) $(id).disabled = loading || exporting || !current?.records.length;
}
function render() {
  const fragment = document.createDocumentFragment();
  for (const record of current.records) {
    const row = node('li', 'log-event');
    const heading = node('div', 'log-event-heading');
    const stamp = node('time', '', new Date(record.timestamp).toLocaleString());
    stamp.dateTime = record.timestamp;
    const result = node('span', 'badge', record.result || record.event);
    result.dataset.result = record.result || '';
    heading.append(stamp, result);
    const title = node('h3', '', [record.container_name || record.alert_name || record.channel || record.category, record.action || record.event].filter(Boolean).join(' · '));
    const summary = node('p', 'log-meta', [record.category, record.event, record.source, record.actor || 'unknown actor', record.host].filter(Boolean).join(' · '));
    const details = node('details');
    details.append(node('summary', '', 'Event details'));
    const values = node('dl');
    for (const key of DETAIL_FIELDS) {
      if (record[key] === null || record[key] === undefined) continue;
      values.append(node('dt', '', key.replaceAll('_', ' ')), node('dd', '', String(record[key])));
    }
    details.append(values);
    row.append(heading, title, summary, details);
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
  filters = next;
  load(null, 0, true);
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
