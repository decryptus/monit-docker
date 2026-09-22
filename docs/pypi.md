# PyPI publishing

The existing `Docker Hub` workflow (`.github/workflows/dockerhub.yml`) builds,
tests and publishes the `monit-docker` Python package alongside stable Docker
releases. Publishing uses GitHub's OIDC identity through PyPI Trusted Publishing;
no PyPI password or API token is stored in GitHub.

## One-time setup

1. In the GitHub repository's Settings > Environments, create `pypi`. Restrict its
   deployment branches and tags to `master` and `v*`. Required reviewers are
   optional if releases should need a manual approval.
2. Sign in to PyPI as an owner of `monit-docker`, open
   [Publishing settings](https://pypi.org/manage/project/monit-docker/settings/publishing/),
   and add a GitHub Trusted Publisher with these exact values:

   | Field | Value |
   | --- | --- |
   | Owner | `decryptus` |
   | Repository | `monit-docker` |
   | Workflow filename | `dockerhub.yml` |
   | Environment | `pypi` |

The workflow filename is not its displayed name and does not include
`.github/workflows/`. A publisher configured for another repository does not
authorize this project. See [PyPI's setup instructions](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).

## Release flow

1. Update `VERSION`, `RELEASE`, `setup.yml`, `monit_docker/__init__.py` and
   `CHANGELOG` together to a new stable `X.Y.Z` version.
2. Merge the release into `master`. The workflow builds a source distribution
   and builds the wheel from that source distribution using `python -m build`.
   It checks both archives with `twine check --strict`, checks their project name
   and version, and installs the wheel in a fresh virtual environment.
3. The regression suite and CLI startup run outside the checkout against that
   installed wheel. Source-only packaging tests and opt-in live Docker tests are
   skipped in this job; the separate Tests workflow covers those checks.
4. After the Python package and Docker image jobs succeed, the workflow selects
   the release, creates its tag when needed and publishes the Docker image.
5. The PyPI job downloads and uploads the already tested distributions. Only this
   job has `id-token: write`, and it runs in the `pypi` environment. Pull requests
   never run publication jobs.

Publishing remains in the same workflow because a tag created with
`GITHUB_TOKEN` does not trigger another workflow. A manually pushed stable tag
also works when its commit includes this workflow and its version matches.
An existing version tag on an ancestor commit skips both publications. Adding
this workflow without increasing the version therefore does not republish
`0.0.53`; the first automatic PyPI release will be the next new version.

## Failures and retries

If package validation fails, neither registry publishes. If Docker Hub publishing
fails, PyPI waits until the Docker Hub job succeeds on a retry. If PyPI publishing
fails, the Docker image and Git tag remain published: fix the Trusted Publisher
configuration or transient service problem, then use **Re-run failed jobs** in
GitHub Actions. This reuses the distributions retained for seven days.

PyPI files are immutable. The publisher uses `skip-existing: true` so a retry
after a partial upload can finish the missing file without replacing existing
files. Never use a retry to ship changed code under an existing version; prepare
a new version instead. After artifact retention expires, create a new release
rather than rebuilding a partially published version with different dependencies.

After a successful release, install the version explicitly:

```sh
python -m pip install 'monit-docker==X.Y.Z'
monit-docker --help
```
