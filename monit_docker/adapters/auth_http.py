"""Browser cookies and explicit native sessions over the same SQLite accounts."""
from httpdis.authentication import AuthenticationRequest, AuthenticationDenied
from httpdis.auth_browser import BrowserAuthProvider
from monit_docker.auth_policy import PATH_SCOPES, username
from monit_docker.account_lifecycle import AccountLifecycle

AUTH_PREFIX = '/v1/auth/'
LOGIN_PATH = AUTH_PREFIX + 'login'
INFO_PATH = AUTH_PREFIX + 'info'
ME_PATH = AUTH_PREFIX + 'me'
LOGOUT_PATH = AUTH_PREFIX + 'logout'
SESSIONS_PATH = AUTH_PREFIX + 'sessions'
USERS_PATH = AUTH_PREFIX + 'users'
PROFILE_PATH = AUTH_PREFIX + 'profile'
PASSWORD_PATH = AUTH_PREFIX + 'password'
RECOVER_PATH = AUTH_PREFIX + 'recover'
DEVICES_PATH = AUTH_PREFIX + 'devices'
UNREGISTER_FIELDS = frozenset(('unregister',))
PASSWORD_FIELDS = frozenset(('current_password', 'new_password'))
RECOVER_FIELDS = frozenset(('username', 'recovery_code', 'new_password'))
AUTH_HEADERS = frozenset(('authorization', 'cookie', 'origin', 'host', 'content-type',
                         'sec-fetch-site', 'sec-fetch-mode', 'x-csrf-token', 'x-monit-client'))
NATIVE_MARKER = 'native-v1'
LOGIN_FIELDS = frozenset(('username', 'password'))
BROWSER_HEADERS = ('origin', 'cookie', 'sec-fetch-site', 'sec-fetch-mode')
SESSION_FIELDS = frozenset(('id',))
USER_FIELDS = {
    'provision': frozenset(('operation', 'username', 'password', 'role')),
    'role': frozenset(('operation', 'username', 'role')),
    'disable': frozenset(('operation', 'username')),
}
PUBLIC_AUTH_PATHS = frozenset((INFO_PATH, LOGIN_PATH, RECOVER_PATH))

def auth_request(handler):
    request = AuthenticationRequest(handler.command, handler._path,
        tuple((key.lower(), value) for key, value in handler.headers.items() if key.lower() in AUTH_HEADERS),
        handler.client_address[0])
    for name in AUTH_HEADERS:
        request.header(name)
    return request

def native_request(request):
    if any(request.header(key) is not None for key in BROWSER_HEADERS):
        raise AuthenticationDenied()

def session_secret(request):
    value = request.header('authorization', '')
    scheme, _, secret = value.partition(' ')
    if scheme.lower() != 'session' or not BrowserAuthProvider._opaque(secret):
        raise AuthenticationDenied()
    return secret

class AuthTransport:
    def __init__(self, accounts, origin, mobile_push=False):
        self.accounts = accounts
        self.lifecycle = AccountLifecycle(accounts)
        from monit_docker.adapters.mobile_push import PushDevices
        self.devices = PushDevices(accounts)
        # CLI/YAML merging uses None for an unspecified optional flag. The API
        # contract always exposes a boolean, including the default-off case.
        self.mobile_push = bool(mobile_push)
        self.origin = origin
        self.browser = BrowserAuthProvider(accounts.service, origin, cookie_prefix='monit')

    def authenticate(self, handler):
        request = auth_request(handler)
        if request.path in PUBLIC_AUTH_PATHS:
            return
        authorization = request.header('authorization', '')
        if authorization.lower().startswith('session '):
            native_request(request)
            identity = self.accounts.service.authenticate_session(session_secret(request),
                request.header('x-csrf-token'), mutation=request.method not in ('GET', 'HEAD'))
        else:
            identity = self.browser.authenticate(request)
        required = PATH_SCOPES.get(request.path)
        if required and required not in identity.scopes:
            raise handler.req_error(403, 'forbidden')
        handler.auth_identity = identity
        handler.audit_actor = identity.principal

    def respond(self, handler):
        # Import only the serializer here, keeping route registration acyclic.
        from monit_docker.adapters.http_routes import json_response
        request = auth_request(handler)
        path = request.path
        if path == INFO_PATH:
            return json_response({'enabled': True, 'api_version': 1, 'native_auth': 'session-v1',
                                  'mobile_push': self.mobile_push, 'account_profile': True, 'password_change': True, 'recovery': 'administrator-code-v1'})
        if path == RECOVER_PATH:
            native_request(request)
            if request.header('x-monit-client') != NATIVE_MARKER or request.header('authorization') is not None:
                raise AuthenticationDenied()
            data = handler.payload_params()
            if not isinstance(data, dict) or set(data) != RECOVER_FIELDS:
                return json_response({'error': 'invalid_request'}, 400)
            self.lifecycle.recover(data['username'], data['recovery_code'], data['new_password'], request.peer)
            return json_response({'updated': True, 'sessions_revoked': True})
        if path == LOGIN_PATH:
            data = handler.payload_params()
            if not isinstance(data, dict) or set(data) != LOGIN_FIELDS:
                return json_response({'error': 'invalid_request'}, 400)
            native = request.header('x-monit-client') == NATIVE_MARKER
            if native:
                native_request(request)
                if request.header('authorization') is not None:
                    raise AuthenticationDenied()
                grant = self.accounts.login(data['username'], data['password'], request.peer)
            else:
                # Validate origin before password work, then rotate the old cookie.
                self.browser.require_browser(request, mutation=True)
                if request.header('authorization') is not None or request.header('x-monit-client') is not None:
                    raise AuthenticationDenied()
                previous = self.browser.session_secret(request)
                grant = self.accounts.login(data['username'], data['password'], request.peer)
                if previous:
                    try:
                        self.accounts.service.logout(previous)
                    except Exception:
                        self.accounts.service.logout(grant.secret)
                        raise
            result = {'username': grant.identity.principal, 'scopes': sorted(grant.identity.scopes),
                      'expires_at': grant.expires_at, 'csrf_token': grant.csrf}
            if native:
                result['session_token'] = grant.secret
            response = json_response(result)
            if not native:
                response.add_header('Set-Cookie', self.browser.cookie(grant.secret, int(self.accounts.service.session_ttl)))
            return response
        identity = handler.auth_identity
        if path == DEVICES_PATH:
            if identity.method != 'session' or 'status:read' not in identity.scopes:
                raise AuthenticationDenied()
            native_request(request)
            secret = session_secret(request)
            if request.method in ('GET', 'HEAD'):
                return json_response(dict(self.devices.status(identity.principal, secret), enabled=self.mobile_push))
            data = handler.payload_params()
            if isinstance(data, dict) and set(data) == UNREGISTER_FIELDS and data['unregister'] is True:
                from monit_docker.adapters.mobile_push import digest
                self.devices.revoke_session(identity.principal, digest(secret))
                return json_response({'revoked': True})
            if not self.mobile_push:
                return json_response({'error': 'push_not_configured'}, 409)
            return json_response(self.devices.register(identity.principal, secret, data))
        if path in (PROFILE_PATH, PASSWORD_PATH):
            if identity.method != 'session':
                raise AuthenticationDenied()
            if path == PROFILE_PATH:
                result = (self.lifecycle.profile(identity.principal) if request.method in ('GET', 'HEAD')
                          else self.lifecycle.update(identity.principal, handler.payload_params()))
                return json_response(result)
            data = handler.payload_params()
            if not isinstance(data, dict) or set(data) != PASSWORD_FIELDS:
                return json_response({'error': 'invalid_request'}, 400)
            self.lifecycle.change_password(identity.principal, data['current_password'], data['new_password'], request.peer)
            return json_response({'updated': True, 'sessions_revoked': True})
        if path == ME_PATH:
            return json_response({'username': identity.principal, 'scopes': sorted(identity.scopes), 'method': identity.method})
        if path == LOGOUT_PATH:
            data = handler.payload_params()
            if data != {}:
                return json_response({'error': 'invalid_request'}, 400)
            native = request.header('authorization', '').lower().startswith('session ')
            secret = session_secret(request) if native else self.browser.session_secret(request)
            if not secret or identity.method != 'session':
                raise AuthenticationDenied()
            from monit_docker.adapters.mobile_push import digest
            self.devices.revoke_session(identity.principal, digest(secret))
            self.accounts.service.logout(secret)
            response = json_response({'revoked': True})
            if not native:
                response.add_header('Set-Cookie', self.browser.expired_cookie())
            return response
        if path == SESSIONS_PATH:
            if request.method in ('GET', 'HEAD'):
                return json_response({'sessions': self.accounts.sessions(identity.principal)})
            data = handler.payload_params()
            if not isinstance(data, dict) or set(data) != SESSION_FIELDS:
                return json_response({'error': 'invalid_request'}, 400)
            self.accounts.revoke(identity.principal, data['id'])
            self.devices.revoke_session(identity.principal, data['id'])
            return json_response({'revoked': True})
        if path == USERS_PATH:
            if request.method in ('GET', 'HEAD'):
                return json_response({'users': self.accounts.list_users()})
            data = handler.payload_params()
            if not isinstance(data, dict):
                return json_response({'error': 'invalid_request'}, 400)
            fields = set(data)
            if data.get('operation') == 'provision' and fields == USER_FIELDS['provision']:
                self.accounts.provision(data['username'], data['password'], data['role'])
            elif data.get('operation') == 'role' and fields == USER_FIELDS['role']:
                self.accounts.set_role(data['username'], data['role'])
            elif data.get('operation') == 'disable' and fields == USER_FIELDS['disable']:
                self.accounts.service.disable(username(data['username']))
            else:
                return json_response({'error': 'invalid_request'}, 400)
            return json_response({'updated': True})
        return json_response({'error': 'not_found'}, 404)

def auth_endpoint(handler):
    from monit_docker.adapters.http_routes import json_response
    auth = handler.server.auth
    if auth is None:
        if handler._path == INFO_PATH:
            return json_response({'enabled': False, 'api_version': 1})
        return json_response({'error': 'not_found'}, 404)
    try:
        return auth.respond(handler)
    except AuthenticationDenied:
        return json_response({'error': 'unauthorized'}, 401)
    except (ValueError, TypeError, UnicodeError):
        return json_response({'error': 'invalid_request'}, 400)
    except Exception:
        return json_response({'error': 'authentication_unavailable'}, 503)
