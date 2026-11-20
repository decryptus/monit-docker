# Local browser dependencies

`i18next.min.js` is the unmodified UMD distribution from the MIT-licensed
`i18next@27.2.2` npm package, fetched from
https://registry.npmjs.org/i18next/-/i18next-27.2.2.tgz.
The accompanying license is in `i18next.LICENSE`.

SHA-256: `4e655c92875eafb59770eeabadba4f79a0c02197e3cd45a1fe09bfdc3f1d9ea5`.

It runs locally under the existing self-only content security policy. No CDN,
tracking service or Node runtime is required by the shipped interface. Incident
catalogs are separate JSON resources in `../locales/`.
