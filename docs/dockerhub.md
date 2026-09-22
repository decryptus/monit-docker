# Docker Hub publishing

On pull requests and pushes to master, the Docker Hub workflow builds a Linux
amd64 image and runs the regression tests, installed package version check and CLI
startup check. After successful validation on master, a missing stable release
tag `vX.Y.Z` is created from VERSION on the tested commit, and the same tested image
is published as
`decryptus/monit-docker:X.Y.Z` and `decryptus/monit-docker:vX.Y.Z`.
Pull requests do not publish images. As in covenant, `latest` is not
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
Merge the release changes into master. The workflow tests the image, creates the
matching tag automatically and publishes the versioned image. There is no manual
`git tag` or `git push` step. The version itself is not incremented automatically.
No extra GitHub secret is required: the publish job uses GITHUB_TOKEN with
contents: write to create the tag. Repository rules must allow this tag creation.

If the version tag already exists on an ancestor commit, publication is skipped.
An existing tag on the exact tested commit permits retrying a failed publication;
a tag outside the commit's history fails the workflow. Existing tags are never
moved. Publication jobs are serialized to prevent competing tag creation.

Manual stable tag pushes remain supported. Automatically created tags do not
trigger another workflow with GITHUB_TOKEN, so Docker Hub publication happens
in the same run (see [GitHub's trigger documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)).
The workflow creates a Git tag; it does not create a GitHub Release page.

Do not move or reuse existing tags. The tagged commit must contain this workflow;
older tags are not published retroactively. Only stable vX.Y.Z tags are accepted.
VERSION and RELEASE must match the tag; the installed package version must match
VERSION. Failed tests or missing credentials prevent publication. After correcting
a credential or transient upload problem, re-run the failed job in Actions.

The image is built from the checked-out source, not the published PyPI package.
The tests simulate Docker API responses; they do not operate on live containers.
The Alpine base and dependencies remain unpinned, so builds are not bit-reproducible.
The image is built for Linux amd64 only, matching covenant's current workflow.
