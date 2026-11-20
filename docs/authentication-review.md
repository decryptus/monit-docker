# SQLite authentication engineering record

## Placement and scope

`auth_policy` owns Monit-Docker roles and route permission vocabulary without CLI or storage imports. `adapters/local_auth` applies that policy to HTTPdis `LocalAuthService`, `Argon2Passwords` and `SQLiteAuthStore`. `adapters/auth_http` handles browser/native transport; `cli_auth` is an offline administration interface. Neutral composition wires the service into the existing HTTPdis server. No Docker operation is performed by account administration or migration.

HTTPdis 0.7.1 already provides transactional SQLite through Sonicprobe AnySQL, credential hashing, expiry, account revisions, token scopes, login throttling and browser origin/CSRF validation. Reuse `HTTPdis[auth]>=0.7.1`; do not fork a generic authentication engine locally. DWho's Memory/Redis session facilities do not replace this local durable account lifecycle. Product roles, htpasswd-to-role mapping and deployment cutover stay in Monit-Docker. The bounded imported-hash verifier reuses HTTPdis's existing crypt verification.

Default legacy operation remains compatible. SQLite is opt-in, requires an existing provisioned database plus HTTPS origin, and rejects legacy shared-secret flags. All API authorization runs on the server. Health/readiness remain private-network health probes. The bundled proxy exposes only approved routes and keeps metrics/notification ingestion on the private agent network.

## Verification

Run the collection guard before the root unittest suite. Added suites cover persistence/restart, expiry, password resets, role revocation, token scope subsets, store failures, explicit migration roles, unsupported hashes, non-overwrite publication, interrupted import, failed backup synchronization, rollback, native/browser requests, CSRF and forged proxy actors. Existing serve test now checks the explicitly injected authentication adapter is absent by default.

`ui/tests/auth.cjs` exercises login, secure-store boundaries, failed and successful logout in a mobile viewport with synthetic API responses. Real cookie flags, server revocation, origin and scope checks are covered by HTTP wire tests. `.github/scripts/check-sqlite-proxy.py` adds real candidate Nginx/TLS acceptance in the existing CI UI job, importing a synthetic legacy account and making no Docker container actions. Existing UI acceptance tests remain enabled for legacy behavior.

The [PR checks](https://github.com/decryptus/monit-docker/pull/119/checks) record results against the exact revision. Local root-suite execution encounters the pre-existing filesystem probe requirement for `/sys`, which is absent in the Work environment; that probe passes on the Linux CI runner. Docker integration cases are explicitly opt-in and run separately in CI. Installed-wheel tests additionally skip source-checkout-only cases.

## Delivery boundaries and remaining work

This branch is a development candidate, not a release or live migration. Published 1.3.3 package/image versions and their registry descriptions remain unchanged; the public demo and public website manuals must keep describing their published version. EN/FR candidate operator guides and the contributor navigation are updated here. Public website generation/deployment belongs to the coordinated release once candidate validation passes.

The APK still needs the native-session client and secure credential migration; its Basic client must not be switched to a SQLite deployment silently. There is no GUI account-management screen, SSO, MFA, per-container authorization or mobile push in this change. Existing htpasswd accounts have no server sessions to migrate. Shared integration tokens require explicit reissue; multiple htpasswd policies require explicit consolidation. Rollback preserves the migrated database but configuration restoration and reconciliation of later account changes remain operator responsibilities.
