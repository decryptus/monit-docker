"""Per-server transport authentication configuration, separate from services."""
from dataclasses import dataclass, field
import re

from monit_docker.domain.errors import MonitoringError

_TOKEN_FILE_RE = re.compile(r'[0-9a-f]{64}\n?')
_TOKEN_READ_CHARS = 67


@dataclass(frozen=True)
class HttpSecurity:
    action_token: str = field(default=None, repr=False)
    action_origin: str = None
    trust_actor: bool = False
    notification_token: str = field(default=None, repr=False)
    audit_token: str = field(default=None, repr=False)


def load_security(*, action_token_file=None, action_origin=None, trust_actor=False,
                  notification_token_file=None, audit_read_token_file=None):
    action_token = read_proxy_token(action_token_file, 'action') if action_token_file else None
    notification_token = read_proxy_token(notification_token_file) if notification_token_file else None
    read_token = read_proxy_token(audit_read_token_file) if audit_read_token_file else None
    if read_token and read_token in (action_token, notification_token):
        raise MonitoringError(110, 'audit read secret must differ from action and notification secrets')
    return HttpSecurity(action_token=action_token, action_origin=action_origin,
                        trust_actor=trust_actor, notification_token=notification_token,
                        audit_token=read_token)


def read_proxy_token(path, purpose='Audit'):
    try:
        with open(path) as stream:
            token = stream.read(_TOKEN_READ_CHARS)
        if not _TOKEN_FILE_RE.fullmatch(token):
            raise ValueError('Invalid token')
        return token.rstrip('\n')
    except (OSError, ValueError, UnicodeError) as error:
        raise MonitoringError(110, '%s token file must contain a 64-character hex secret' % purpose) from error
