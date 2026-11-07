# monit-docker 1.3.2

Require HTTPdis 0.6.39 or newer in the agent package.

The minimum HTTPdis version now includes the HTTP request and error-response
fixes through 0.6.39. The Compose agent continues using the filtered Docker socket.
With HTTPdis 0.7.0 or later, migrate legacy SHA-1 and DES htpasswd entries before
upgrading. See the [HTTPdis migration notes](https://github.com/decryptus/httpdis/blob/master/docs/release-0.7.0.md).
