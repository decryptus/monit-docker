"""Product authorization vocabulary, independent of HTTP and credential storage."""
import re

USERNAME_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')
ROLES = {
    'viewer': frozenset(('status:read',)),
    'operator': frozenset(('status:read', 'audit:read', 'actions:write', 'ai:use')),
    'admin': frozenset(('status:read', 'audit:read', 'actions:write', 'ai:use', 'auth:manage', 'metrics:read')),
    'metrics': frozenset(('metrics:read',)),
    'receiver': frozenset(('notifications:write',)),
}
ALL_SCOPES = frozenset().union(*ROLES.values(), ('notifications:write',))
PATH_SCOPES = {
    '/v1/status': 'status:read', '/metrics': 'metrics:read',
    '/v1/audit': 'audit:read', '/v1/audit/export': 'audit:read',
    '/v1/actions': 'actions:write', '/v1/notifications': 'notifications:write',
    '/v1/ai/explain': 'ai:use', '/v1/ai/preview': 'ai:use',
    '/v1/auth/users': 'auth:manage',
}

def username(value):
    if not isinstance(value, str) or USERNAME_RE.fullmatch(value) is None:
        raise ValueError('invalid account identifier')
    return value

def role_scopes(role):
    if not isinstance(role, str) or role not in ROLES:
        raise ValueError('invalid account role')
    return ROLES[role]

def role_name(scopes):
    return next((role for role, values in ROLES.items() if values == set(scopes)), 'service')
