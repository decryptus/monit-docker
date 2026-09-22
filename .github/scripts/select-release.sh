#!/usr/bin/env bash
# Resolve the release without mutating refs; run only after image tests pass.
set -euo pipefail

version="$(cat VERSION)"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Expected VERSION X.Y.Z' >&2; exit 1; }
test "$version" = "$(cat RELEASE)"
tag="v$version"
sha="$(git rev-parse HEAD)"
create=false
publish=false

if [[ "$GITHUB_REF" == refs/tags/* ]]; then
  test "${GITHUB_REF#refs/tags/}" = "$tag"
  publish=true
elif [[ "$GITHUB_REF" == refs/heads/master ]]; then
  if git show-ref --verify --quiet "refs/tags/$tag"; then
    tagged_sha="$(git rev-parse "$tag^{commit}")"
    if [[ "$tagged_sha" == "$sha" ]]; then
      # Retry this exact release after a partial or failed Docker Hub upload.
      publish=true
    else
      # Ordinary commits on an already tagged version must not replace its image.
      git merge-base --is-ancestor "$tagged_sha" "$sha" || {
        echo "$tag points outside this commit's history; refusing release" >&2
        exit 1
      }
      echo "$tag already exists on an earlier commit; skipping publication"
    fi
  else
    create=true
    publish=true
  fi
fi

{
  echo "tag=$tag"
  echo "create=$create"
  echo "publish=$publish"
} >> "$GITHUB_OUTPUT"
