# Self-service accounts and optional remote notifications

These additions require server 2.0.1 or later (current release: 2.0.3). Older servers
continue to support login and monitoring but return 404 for the new endpoints.
SQLite authentication remains opt-in. Anonymous demonstration access is a separate
read-only proxy configuration; never disable authentication on a private agent.

Users can read/update their display name and preferred language (`system`, `en`,
`fr`) with a session. Usernames and roles remain administrator-managed. Changing
the password requires the current password and CSRF proof, preserves the role,
and revokes all sessions, machine tokens and mobile registrations. Reissue machine
tokens deliberately after a password change. Nothing retries a password mutation.

## Password recovery without email

The administrator verifies the requester's identity using an existing trusted
channel, then runs locally on the server:

```sh
monit-docker auth recovery-code --database /private/auth.db --username alice --output-file /private/recovery-code
```

Send the file's code privately to that user. It expires after 15 minutes and can
be used once with a new password of at least 12 characters. Creating a replacement
code invalidates the previous code. Only its SHA-256 digest is persisted. The
command never prints the code and requires a new private output file. Remove that
file after handoff. No SMTP server or verified email identity is assumed, and no
email is sent. Disabled accounts cannot recover. Login rate limits also protect
code verification. Password changes and recovery revoke existing credentials.

## Optional background delivery

Enable `serve --mobile-push` (or `serve.mobile_push: true` in YAML) only with
`--auth-db` and `--auth-origin`. The server needs outbound HTTPS access to
`https://exp.host`. The Android client needs matching Expo/Firebase configuration
and notification permission; an unconfigured build must show this limitation.
The normal monitoring engine and other alert transports remain independent.

Each account can register five devices. A registration lasts seven days, is bound
to its account/revision and registering session, and can be renewed explicitly.
Logout and individual session revocation remove corresponding registrations;
password/role changes remove all of them. Offline logout cannot guarantee remote
revocation: reconnect and revoke the old session, or let the device lease expire.
Session expiry requires login again but does not silently cancel the separately
consented seven-day notification lease.

The server observes cached monitoring state every 30 seconds in a separate worker.
It groups unavailable monitoring and unhealthy/exited/dead/restarting containers,
excludes maintenance, deduplicates episodes and backs off failed sends for five
minutes. These server criteria differ from foreground client filters/dismissals.
Notifications contain a generic localized message and the installation origin,
never workload names, metrics, credentials or an executable action. Expo/Google
process the delivery token and payload; registration is explicit and optional.
An accepted ticket is not delivery. Receipts are checked after 15 minutes and
invalid tokens are removed. A successful receipt confirms provider handoff, not
that the user saw the notification. Phone/network/OS restrictions still apply;
Android force-stop prevents delivery until the app is reopened.

## API additions

All routes use API v1, bounded JSON and no-store responses. Private writes require
`Authorization: Session ...` and `X-CSRF-Token`. Native requests carry no browser
cookies/origin. No machine token can edit a human profile or register a phone.

| Route | Method | Body / response |
| --- | --- | --- |
| `/v1/auth/info` | GET | Adds account/password capabilities, `recovery: administrator-code-v1`, `mobile_push` |
| `/v1/auth/profile` | GET, POST | Exactly `display_name` and `language`; authenticated user's own profile |
| `/v1/auth/password` | POST | `current_password`, `new_password`; returns `updated`, `sessions_revoked` |
| `/v1/auth/recover` | POST | `username`, `recovery_code`, `new_password`; requires `X-Monit-Client: native-v1`, rejects browser/mixed credentials |
| `/v1/auth/devices` | GET | `enabled`, current session's device expiry/delivery statuses; no tokens |
| `/v1/auth/devices` | POST | `token`, `language` (`en`/`fr`), `channel` (`monit-observations-S-V`, S/V 0 or 1), or `unregister: true` |

Sessions still have a maximum lifetime of eight hours and a 30-minute idle expiry.
Activity extends idle validity within the absolute limit. There is no refresh
credential or QR pairing endpoint; renewed authentication requires the password.
