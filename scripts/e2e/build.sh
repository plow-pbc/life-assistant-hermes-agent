#!/usr/bin/env bash
# Pull the pinned base and build this repo's image. Needed once, and again
# whenever the Dockerfile or the base pin changes -- NOT per iteration: the
# skills are bind-mounted, so an edit to one needs only run-agent.sh.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# The pull is not optional on this registry. ECR Public answers HEAD on a
# digest reference with 403 while answering GET normally, and BuildKit resolves
# a FROM with HEAD -- so a clean build fails at metadata resolution until the
# digest is in the local store, and `docker pull` takes the GET path.
docker pull "$BASE_IMAGE"

# --platform: the base is published linux/amd64 only and this is an arm64 Mac.
# Naming it keeps the build (and every later run) on one emulated arch instead
# of failing to find a manifest.
docker build --platform linux/amd64 -t "$IMAGE" "$REPO_DIR"
