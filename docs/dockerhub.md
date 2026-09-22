# Docker Hub publishing

On pull requests and pushes to master, the Docker Hub workflow builds a Linux
amd64 image and runs the regression tests, installed package version check and CLI
startup check. A new stable release tag `vX.Y.Z` publishes the same tested image as
`decryptus/monit-docker:X.Y.Z` and `decryptus/monit-docker:vX.Y.Z`.
Branches and pull requests do not publish images. As in covenant, `latest` is not
updated: use an explicit image version when deploying a release.

## One-time setup

1. Ensure the Docker Hub repository `decryptus/monit-docker` exists.
2. Make a Docker Hub access token with read/write access to that repository.
3. In this GitHub repository, Settings > Secrets and variables > Actions, add
   `DOCKERHUB_TOKEN`. The workflow authenticates as `decryptus`.

A repository secret in covenant is not automatically available in monit-docker.
An organization secret must explicitly allow this repository. Never commit the
token or put it in workflow logs.

## Release

Update VERSION, RELEASE, setup.yml, monit_docker/__init__.py and CHANGELOG consistently.
Merge the workflow and release changes into master and wait for image validation.
From that release commit, create and push a new tag matching VERSION:

```sh
release_version="$(cat VERSION)"
git tag -a "v${release_version}" -m "version: ${release_version}"
git push origin "v${release_version}"
```

Do not move or reuse existing tags. The tagged commit must contain this workflow;
older tags are not published retroactively. Only stable vX.Y.Z tags are accepted.
VERSION and RELEASE must match the tag; the installed package version must match
VERSION. Failed tests or missing credentials prevent publication. After correcting
a credential or transient upload problem, re-run the failed job in Actions.

The image is built from the checked-out source, not the published PyPI package.
The tests simulate Docker API responses; they do not operate on live containers.
The Alpine base and dependencies remain unpinned, so builds are not bit-reproducible.
The image is built for Linux amd64 only, matching covenant's current workflow.
