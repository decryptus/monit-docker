# Local accounts, sessions and migration (development candidate)

Available since **2.0.0**. Use matching 2.0.0 agent and UI images. Enabling SQLite is an explicit configuration change; upgrading the package alone keeps the existing authentication mode. See the [French guide](authentication-fr.md).

## Accounts and rights

Accounts, password hashes, sessions, API token digests and login throttling live in one local SQLite database. No Redis or hosted broker is needed. The database must be on a local filesystem in a directory owned by the daemon user, without group/other write permission. Keep the database at mode `0600`, its directory at `0700`, and mount the **directory** persistently when using containers. Do not publish the agent port on the Internet.

| Role | Permissions |
| --- | --- |
| `viewer` | Container status only |
| `operator` | Status, journal, enabled manual actions and incident assistance |
| `admin` | Operator permissions, accounts and metrics |
| `metrics` | Metrics only; dedicated machine account |
| `receiver` | Notification ingestion only; dedicated machine account |

Rights apply to the entire local instance, not individual hosts or containers. Granting a role does not enable actions, the journal or AI: their existing explicit configuration is still required. The server enforces permissions; proxy identity headers are ignored in SQLite mode.

Create the directory and first account **as the daemon user**:

```sh
install -d -m 700 /var/lib/monit-docker/auth
monit-docker auth user-add --database /var/lib/monit-docker/auth/auth.db --username administrator --role admin
monit-docker auth users --database /var/lib/monit-docker/auth/auth.db
monit-docker serve --auth-db /var/lib/monit-docker/auth/auth.db --auth-origin https://monitor.example
```

`user-add` prompts for a password (12 characters minimum, 1024 UTF-8 bytes maximum). On an existing username it **resets the password, replaces the role, enables the account and revokes all its sessions and API tokens**. `--password-file` accepts a private file owned by the current user. Never pass passwords in arguments or commit them.

Use `auth role --username NAME --role ROLE` or `auth disable --username NAME`, always with `--database PATH`. Both revoke existing sessions and tokens. Local CLI administration remains available for recovery if the last administrator is disabled. HTTPS administrators can also use `/v1/auth/users` (see below); there is no web account-management screen yet.

## Web and reverse proxy

Use `ui/nginx/sqlite.conf` instead of the legacy Basic-auth `default.conf`, mounted at `/etc/nginx/conf.d/default.conf`. The matching UI image includes `monit-sqlite-proxy.conf`. Supply the existing TLS certificate/key mounts and keep the private `monit-docker:9808` upstream. `--auth-origin` must exactly match the public HTTPS origin, including a non-default port. The proxy must preserve Host (including port), Cookie, Authorization, Origin, fetch metadata and CSRF headers. Do not add CORS or Basic authentication to this configuration.

The web interface displays a login dialog. Its session cookie is `Secure`, `HttpOnly`, `SameSite=Strict` and scoped to `/`; JavaScript cannot read the credential. Only the CSRF token is kept in the tab's `sessionStorage`. A new tab without that token asks for authentication again. Successful login replaces that browser's previous session. Sign-out revokes the server session; a network failure is reported instead of claiming successful revocation.

Sessions expire after 8 hours, or 30 minutes without an authenticated request. Polling counts as activity. Restarting the server preserves valid sessions. Password/role changes and disabling an account revoke them immediately. New passwords use Argon2id; only digests of session/API/CSRF secrets are persisted. Database failure returns `503`, never anonymous access. Login attempts are limited by account, direct network peer and globally; clients behind the same proxy share its peer limit.

Add `--auth-audit --audit-file PATH` to expose the journal to allowed roles; `--auth-notifications` enables authenticated ingestion. Manual actions still require `--allow-actions --state-file PATH`. AI still requires its explicit provider configuration. Existing safety rules, protected containers and action limits remain in force.

## Native APK and machine clients

The existing Basic-auth APK cannot authenticate to this mode. Update its client before cutting over. Public-demo reading and native session authentication are separate modes; never silently fall back to Basic or an anonymous endpoint after authentication fails.

| Request | Contract |
| --- | --- |
| `GET /v1/auth/info` | Public discovery: `enabled`, `api_version: 1`, `native_auth: session-v1` |
| `POST /v1/auth/login` | JSON `username`, `password`; native clients send `X-Monit-Client: native-v1`, without Cookie, Origin or browser fetch headers |
| Native login response | `session_token`, `csrf_token`, `expires_at`, `username`, `scopes` |
| Authenticated native request | `Authorization: Session <session_token>`; mutations also send `X-CSRF-Token` |
| `GET /v1/auth/me` | Current username, scopes and authentication method |
| `POST /v1/auth/logout` | JSON `{}`; revokes current session |
| `GET /v1/auth/sessions` | Current account's active sessions, with revocation IDs and expiry times |
| `POST /v1/auth/sessions` | JSON `{"id":"REVOCATION_ID"}`; revoke one of your sessions |
| `GET /v1/auth/users` | Administrator-only account list, without password hashes |
| `POST /v1/auth/users` | `operation: provision` with username/password/role; `role` with username/role; or `disable` with username |

All JSON requests require `Content-Type: application/json`. Reject unexpected server origins and HTTPS errors in native clients. Store session/CSRF credentials in the operating system secure store, not the password. Do not send native credentials with browser cookies. `401` requires login again; `403` means insufficient permission; `503` means authentication is unavailable.

Machine integrations use scoped `Bearer` API tokens, not session tokens. Example for a previously provisioned `metrics` account:

```sh
monit-docker auth token --database /var/lib/monit-docker/auth/auth.db --username prometheus --scope metrics:read --ttl 86400 --output-file /private/prometheus-token
```

The output file must not exist; it is created at `0600`. Token lifetime is 1 second to 30 days (default 1 day). Arrange renewal before expiry. Old shared proxy/status/metrics/notification secrets are not account identities and are **not imported**. Reissue tokens with explicit scopes and reconfigure clients before cutover. Those legacy flags cannot be mixed with `--auth-db`.

## Migrate htpasswd accounts

1. Inventory **all** htpasswd files and their access rules, including separate journal/AI/mobile files. The command imports one complete account file. For multiple files, prepare a consolidated private file offline: resolve duplicate names/passwords explicitly; never silently merge conflicting accounts. Choose a role for every account, without granting administrator access by default. Stop account changes during migration.
2. Save the current daemon/Compose, proxy and client configuration privately. Preserve the original htpasswd files and shared token files for rollback. The tool backs up the source account file and role map; it does not discover or back up deployment configuration.
3. Prepare a role map such as `{"alice":"viewer","operator":"operator"}` in a private JSON file. It must match the source usernames exactly. Names use ASCII letters/digits then letters/digits/`.`/`_`/`-`, at most 64 characters. Existing nonmatching names need an explicit rename and client update.
4. Validate without writing anything:

```sh
monit-docker auth migrate --database /var/lib/monit-docker/auth/auth.db --htpasswd /private/users.htpasswd --roles-file /private/roles.json
```

5. Apply with a **new** backup directory. Run as the future database owner; create the private database parent first:

```sh
monit-docker auth migrate --database /var/lib/monit-docker/auth/auth.db --htpasswd /private/users.htpasswd --roles-file /private/roles.json --backup-dir /private/auth-backup-001 --apply
```

The tool validates the whole input, backs it up at private permissions with SHA-256 checksums, synchronizes backup files and directory entries to disk, imports into a temporary database in one transaction, checks counts/integrity and publishes without replacing an existing database. It rechecks inputs before publication. Original files remain unchanged. A failed run is not an invitation to delete an existing database; inspect the result and use a new backup directory for a new attempt.

Supported imported hashes are SHA-256/512 crypt and bounded-cost bcrypt. APR1, SHA-1, DES, plaintext, malformed or excessive-cost hashes fail the entire migration. Reset unsupported accounts explicitly before import. Passwords are preserved and strengthened to Argon2id after successful login when they meet the new minimum length. Short legacy passwords require an explicit password reset. Basic authentication has no server session to import: users log in again; browser-cached Basic credentials are not migrated.

6. Provision an administrator if the imported map contains none; issue machine tokens. Start a private candidate instance and verify correct/incorrect passwords, viewer denial, administrator access, logout/revocation and restart persistence. Only then stop the old instance and switch the daemon and proxy configurations together. Keep monitoring/action state and audit data separate from the authentication database.

## Rollback and backups

Stop **all** processes writing this database. Restore the saved legacy daemon/proxy/client configuration, with the original access policy; do not make the API anonymous as a workaround. Before restarting, optionally retire the migrated database:

```sh
monit-docker auth rollback --database /var/lib/monit-docker/auth/auth.db --backup-dir /private/auth-backup-001 --writers-stopped
```

This verifies the migration backup and database schema, then renames the database to a unique `.retired-*` file. It preserves post-cutover accounts/sessions for recovery; it does not copy those changes back to htpasswd or automatically restore configuration. Reconcile any account disables/password changes before enabling old credentials again. Keep the required matching APK/client version for the restored mode.

For routine SQLite backup, stop writers before copying the database, or use SQLite's backup API. Do not copy a live database file alone. Keep backups private and encrypted where appropriate: they contain password hashes and live credential digests. Unknown database schema versions are rejected; never downgrade a live database in place.

## Web sign-in language

The branded sign-in form offers French and English before login and in the account toolbar. The browser language is the initial default; an explicit choice is remembered locally. English is the fallback. Passwords are never saved by this selector. The same preference controls AI explanations; other dashboard views retain their existing language. Public demo access is configured separately from authenticated installations.

See [account lifecycle and remote notifications](mobile-accounts.md).
