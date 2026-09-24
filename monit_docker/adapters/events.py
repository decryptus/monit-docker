"""One bounded Docker history query per cycle, only for requested event checks.

Read the unfiltered daemon buffer to detect retention overflow before selecting
containers. Filtering at the daemon would hide eviction by unrelated workloads.
No background stream or additional persistent state is needed by cron or serve.
"""

import json
import time

from monit_docker.domain.errors import MonitoringError

_HISTORY_LIMIT = 256
_MAX_BYTES = 2 * 1024 * 1024
_MAX_LINE = 65536
_READ_TIMEOUT = 5
_READ_CHUNK = 1024
_EVENT_FIELDS = {'oom': 'oom_events', 'start': 'starts_recent'}
_NANOSECONDS = 1000000000


def read_history(api, until):
    # SDK events() forces an infinite HTTP timeout. Use its authenticated
    # transport with an explicit idle timeout and a cutoff in the past instead.
    response = api.get(api._url('/events'), params={'since': '1', 'until': str(until)},
                       stream=True, timeout=_READ_TIMEOUT)
    try:
        response.raise_for_status()
        events, pending, total = [], bytearray(), 0
        deadline = time.monotonic() + _READ_TIMEOUT
        for chunk in response.iter_content(chunk_size=_READ_CHUNK):
            total += len(chunk)
            if total > _MAX_BYTES or time.monotonic() > deadline:
                raise ValueError('event history exceeds read budget')
            pending.extend(chunk)
            while b'\n' in pending:
                line, _, pending = pending.partition(b'\n')
                if not line or len(line) > _MAX_LINE or len(events) >= _HISTORY_LIMIT:
                    raise ValueError('invalid or oversized event history')
                events.append(json.loads(line))
            if len(pending) > _MAX_LINE:
                raise ValueError('oversized event')
        if pending:
            raise ValueError('incomplete event history')
        return events
    finally:
        response.close()


def event_counts(events, identifiers, window, until):
    """Count starts (including initial start) and OOMs in (until-window, until]."""
    since_ns, until_ns = (until - window) * _NANOSECONDS, until * _NANOSECONDS
    result = {identifier: dict(oom_events=0, starts_recent=0) for identifier in identifiers}
    seen, stamps = set(), []
    if len(events) > _HISTORY_LIMIT:
        raise ValueError('too many Docker events')
    for event in events:
        if not isinstance(event, dict):
            raise ValueError('invalid Docker event')
        stamp = event.get('timeNano')
        if stamp is None:
            seconds = event.get('time')
            stamp = seconds * _NANOSECONDS if type(seconds) is int else None
        if type(stamp) is not int or stamp < 0 or stamp > until_ns:
            raise ValueError('invalid Docker event time')
        stamps.append(stamp)
        if event.get('Type') != 'container':
            continue
        actor = event.get('Actor')
        if not isinstance(actor, dict) or not isinstance(actor.get('ID'), str):
            raise ValueError('invalid Docker event actor')
        identifier, action = actor['ID'], event.get('Action')
        if not isinstance(action, str):
            raise ValueError('invalid Docker event action')
        key = (identifier, action, stamp)
        if key in seen:
            continue
        seen.add(key)
        if identifier in result and since_ns < stamp <= until_ns and action in _EVENT_FIELDS:
            result[identifier][_EVENT_FIELDS[action]] += 1
    complete = len(events) < _HISTORY_LIMIT or min(stamps) <= since_ns
    for values in result.values():
        if not complete:
            values.update(oom_events=None, starts_recent=None)
        values.update(event_window_seconds=window, event_window_end=until,
                      event_history_complete=int(complete))
    return result


def collect_events(api, identifiers, window, now=None):
    # Past whole seconds avoid accidentally opening a live stream at the cutoff.
    until = int(time.time() if now is None else now) - 1
    try:
        return event_counts(read_history(api, until), identifiers, window, until)
    except Exception as error:
        raise MonitoringError(115, 'Docker event history unavailable (%s)' % type(error).__name__) from error
