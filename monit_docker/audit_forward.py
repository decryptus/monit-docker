"""Explicit HTTPS export. Local records remain authoritative on delivery failure."""
from pathlib import Path
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from monit_docker.audit import AuditError, encoded, prepare_record


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_events(records, url, token_file=None, timeout=5):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or any(c.isspace() or ord(c) < 32 for c in url):
        raise ValueError('Audit destination must be an HTTPS URL without embedded credentials')
    headers = {'Content-Type': 'application/json'}
    if token_file:
        token = Path(token_file).read_text().strip()
        if not token or len(token) > 4096 or any(c.isspace() for c in token):
            raise ValueError('Invalid audit destination token')
        headers['Authorization'] = 'Bearer ' + token
    opener = urllib.request.build_opener(NoRedirect())
    sent = 0
    for record in records:
        record = prepare_record(record)
        request = urllib.request.Request(url, data=encoded(record), headers=dict(headers, **{'Idempotency-Key': record['event_id']}), method='POST')
        try:
            with opener.open(request, timeout=timeout) as response:
                if not 200 <= response.status < 300:
                    raise AuditError('Destination did not acknowledge the audit event')
        except (OSError, urllib.error.HTTPError) as error:
            # Never print URL, token, response body or exception text.
            raise AuditError('Audit forwarding failed after %d acknowledged events; local journal retained' % sent) from error
        sent += 1
    return sent
