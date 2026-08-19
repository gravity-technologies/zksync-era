#!/usr/bin/env bash
#
# Build the prover-jobs-fri-datafix image for the target runtime platform.
# Usage: ./build.sh <version>   (e.g. v1.0.0)
#
# The batch range / dry-run flag are NOT baked in at build time: they are passed
# as env vars by the Kubernetes Job (see the zkprod-gitops chart), so one image
# build serves every range.
set -euo pipefail

cd "$(dirname "$0")"

VERSION="${1:?usage: ./build.sh <version>  (e.g. v1.0.0)}"
IMAGE_NAME="${IMAGE_NAME:-prover-jobs-fri-datafix}"
PLATFORM="${PLATFORM:-linux/amd64}"

if docker buildx version >/dev/null 2>&1; then
    docker buildx build --platform "$PLATFORM" --load -t "${IMAGE_NAME}:${VERSION}" .
else
    cat >&2 <<EOF_ERR
Docker Buildx is not installed or is not visible to the Docker CLI.

This image must be built for ${PLATFORM}; otherwise an ARM Mac can publish an
image that fails on AMD64 GCP nodes with:

  exec /app/entrypoint.sh: exec format error

Install Docker Buildx, then rerun:

  ./build.sh ${VERSION}
EOF_ERR
    exit 1
fi

echo ">> Built ${IMAGE_NAME}:${VERSION} for ${PLATFORM}"
