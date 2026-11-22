"""Local administrator interface; no Docker, HTTP request or proxy secret required."""
import getpass
import json
import os
from pathlib import Path
import sys

from monit_docker.auth_policy import ROLES, ALL_SCOPES, username

OPERATIONS = ('user-add', 'users', 'role', 'disable', 'token', 'migrate', 'rollback', 'recovery-code')

class MonitDockerSubCmdAuth:
    CMD_NAME = 'auth'

    def __init__(self, options):
        self.options = options

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = subparsers.add_parser(cls.CMD_NAME, help='manage local SQLite accounts and migrate legacy users offline')
        parser.add_argument('operation', choices=OPERATIONS)
        parser.add_argument('--database', required=True)
        parser.add_argument('--username', help='email address for new accounts; existing legacy identifiers remain supported')
        parser.add_argument('--role', choices=tuple(ROLES))
        parser.add_argument('--password-file', help='private UTF-8 file; otherwise prompt without echo')
        parser.add_argument('--scope', action='append', choices=sorted(ALL_SCOPES), default=[])
        parser.add_argument('--ttl', type=int, default=86400)
        parser.add_argument('--output-file', help='new private file receiving a one-time API token')
        parser.add_argument('--htpasswd')
        parser.add_argument('--roles-file')
        parser.add_argument('--backup-dir')
        parser.add_argument('--apply', action='store_true', help='apply migration (default: validation only)')
        parser.add_argument('--writers-stopped', action='store_true', help='acknowledge server shutdown before rollback')
        return parser

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        if not options.database.strip():
            parser.error('--database must not be empty')
        if options.operation in ('user-add', 'role', 'disable', 'token', 'recovery-code'):
            try:
                username(options.username)
            except ValueError:
                parser.error('a valid --username is required')
        if options.operation in ('user-add', 'role') and not options.role:
            parser.error('--role is required; no default administrative rights are granted')
        if options.operation == 'migrate' and not (options.htpasswd and options.roles_file):
            parser.error('--htpasswd and --roles-file are required')
        if options.operation == 'rollback' and not (options.backup_dir and options.writers_stopped):
            parser.error('--backup-dir and --writers-stopped are required')
        if options.operation == 'recovery-code' and not options.output_file:
            parser.error('recovery-code requires --output-file')
        if options.operation == 'token' and not (options.output_file and options.scope and 1 <= options.ttl <= 2592000):
            parser.error('token requires --output-file, --scope and --ttl between 1 and 2592000')

    def __call__(self):
        from monit_docker.adapters.local_auth import Accounts
        from monit_docker.adapters.auth_migration import migrate, rollback, read_file, private_write
        options = self.options
        accounts = None
        try:
            if options.operation == 'migrate':
                result = migrate(options.htpasswd, options.roles_file, options.database, options.backup_dir, apply=options.apply)
            elif options.operation == 'rollback':
                result = rollback(options.database, options.backup_dir, writers_stopped=options.writers_stopped)
            else:
                accounts = Accounts(options.database, create=options.operation == 'user-add')
                if options.operation == 'user-add':
                    if options.password_file:
                        path = Path(options.password_file)
                        if path.stat().st_mode & 0o077 or path.stat().st_uid != os.geteuid():
                            raise ValueError('password file must be private and owned by the current user')
                        password = read_file(path, 1025).decode().removesuffix('\n')
                    else:
                        password = getpass.getpass('Password: ')
                        if password != getpass.getpass('Confirm password: '):
                            raise ValueError('password confirmation differs')
                    accounts.provision(options.username, password, options.role)
                    result = {'updated': options.username, 'sessions_revoked': True}
                elif options.operation == 'recovery-code':
                    from monit_docker.account_lifecycle import AccountLifecycle, RECOVERY_TTL
                    lifecycle = AccountLifecycle(accounts)
                    secret = lifecycle.issue_recovery(options.username)
                    try:
                        private_write(options.output_file, (secret + '\n').encode())
                    except Exception:
                        lifecycle.discard_recovery(options.username)
                        raise
                    result = {'recovery_code_created': True, 'expires_in': RECOVERY_TTL}
                elif options.operation == 'users':
                    result = {'users': accounts.list_users()}
                elif options.operation == 'role':
                    accounts.set_role(options.username, options.role)
                    result = {'updated': options.username, 'sessions_revoked': True}
                elif options.operation == 'disable':
                    accounts.service.disable(options.username)
                    result = {'disabled': options.username, 'sessions_revoked': True}
                else:
                    grant = accounts.service.issue_token(options.username, options.scope, options.ttl)
                    try:
                        private_write(options.output_file, (grant.secret + '\n').encode())
                    except Exception:
                        accounts.service.revoke_token(grant.credential_id)
                        raise
                    result = {'credential_id': grant.credential_id, 'expires_at': grant.expires_at}
            print(json.dumps(result, sort_keys=True))
            return 0
        except Exception:
            print('Authentication operation failed; check paths, permissions and migration inputs. No credentials were printed.', file=sys.stderr)
            return 119
        finally:
            if accounts is not None:
                accounts.close()
