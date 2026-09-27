"""Decode journal HTTP queries and map service errors to HTTP status codes."""
from urllib.parse import parse_qs

from monit_docker.audit_query import AuditQuery, FILTER_KEYS, QueryError

_MAX_QUERY = 32768
_QUERY_KEYS = frozenset(FILTER_KEYS + ('cursor', 'format'))
_EXPORT_FORMATS = ('jsonl', 'csv')
_ERROR_STATUS = {'invalid_filters': 400, 'invalid_cursor': 400,
                 'cursor_expired': 410, 'audit_busy': 503, 'audit_unavailable': 503}


def query_error_status(error):
    return _ERROR_STATUS.get(error.reason, 503)


def parse_query(query):
    if not isinstance(query, str) or len(query) > _MAX_QUERY:
        raise QueryError('invalid_filters')
    try:
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True,
                          max_num_fields=len(_QUERY_KEYS)) if query else {}
    except ValueError:
        raise QueryError('invalid_filters') from None
    if set(values) - _QUERY_KEYS or any(len(value) != 1 for value in values.values()):
        raise QueryError('invalid_filters')
    values = {key: value[0] for key, value in values.items() if value[0]}
    format = values.get('format', 'jsonl')
    if format not in _EXPORT_FORMATS:
        raise QueryError('invalid_filters')
    filters = {key: values[key] for key in FILTER_KEYS if key in values}
    return AuditQuery(filters, values.get('cursor')), format


def read_page(reader, query, actor):
    request, format = parse_query(query)
    return reader.page(request, actor), format
