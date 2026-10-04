"""Bounded Docker statistics response bodies, without abandoned reader threads."""
import socket
import logging
import sys
from threading import Event, Timer

from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout as RequestsTimeout
from urllib3.exceptions import ReadTimeoutError

from monit_docker.domain.errors import MonitoringError

STATS_TIMEOUT = 5.0
MAX_STATS_BYTES = 2 * 1024 * 1024
MAX_STATS_SAMPLES = 8
_CHUNK_SIZE = 1024
LOG = logging.getLogger('monit-docker')


def _transport_timed_out(error):
    # Requests wraps urllib3's streaming read timeout in ConnectionError.
    # Inspect its typed cause, not arbitrary error text or unrelated contexts.
    return (isinstance(error, (RequestsTimeout, TimeoutError, ReadTimeoutError))
            or (isinstance(error, RequestsConnectionError) and bool(error.args)
                and isinstance(error.args[0], ReadTimeoutError)))


def read_stats(container):
    api = container.client.api
    timeout = min(api.timeout, STATS_TIMEOUT) if api.timeout is not None else STATS_TIMEOUT
    try:
        response = api._get(api._url('/containers/{0}/stats', container.id),
                            params={'stream': True}, stream=True, timeout=timeout)
    except (RequestsTimeout, TimeoutError, ReadTimeoutError):
        raise MonitoringError(115, 'Docker statistics sampling timed out') from None
    timer = None
    expired = Event()
    try:
        api._raise_for_status(response)
        # Use the SDK's transport selection for Unix, TCP, TLS and SSH sockets.
        raw = api._get_raw_response_socket(response)
        transport = getattr(raw, '_sock', raw)
        transport = getattr(transport, 'socket', transport)

        def interrupt():
            expired.set()
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = Timer(STATS_TIMEOUT, interrupt)
        timer.daemon = True
        timer.start()
        pending = bytearray()
        received = 0
        try:
            for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                if expired.is_set():
                    raise MonitoringError(115, 'Docker statistics sampling timed out')
                received += len(chunk)
                if received > MAX_STATS_BYTES:
                    raise MonitoringError(115, 'Docker statistics response exceeds size limit')
                pending.extend(chunk)
                while b'\n' in pending:
                    line, _, remainder = pending.partition(b'\n')
                    pending = bytearray(remainder)
                    if line.strip():
                        yield bytes(line)
            if expired.is_set():
                raise MonitoringError(115, 'Docker statistics sampling timed out')
            if pending.strip():
                yield bytes(pending)
        except Exception as error:
            if expired.is_set() or _transport_timed_out(error):
                raise MonitoringError(115, 'Docker statistics sampling timed out') from None
            raise
    finally:
        failed = sys.exc_info()[0] not in (None, GeneratorExit)
        if timer is not None:
            timer.cancel()
            timer.join()
        try:
            response.close()
        except Exception:
            if not failed:
                raise
            LOG.exception('stats response cleanup failed; preserving the sampling error')
