#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

VERSION="${1:?usage: ./push.sh <version> [env] [local-image]}"
ENVIRONMENT="${2:-testnet}"
LOCAL_IMAGE="${3:-prover-jobs-fri-datafix:${VERSION}}"
EXPECTED_PLATFORM="${EXPECTED_PLATFORM:-linux/amd64}"

# Not committed: set REGISTRY in the environment, or in docker/registry.env.
REGISTRY_ENV_FILE="${REGISTRY_ENV_FILE:-../registry.env}"
if [ -z "${REGISTRY:-}" ] && [ -f "$REGISTRY_ENV_FILE" ]; then
    # shellcheck disable=SC1090
    . "$REGISTRY_ENV_FILE"
fi
: "${REGISTRY:?REGISTRY must be set (export it, or put REGISTRY=... in docker/registry.env)}"

REMOTE="${REGISTRY}/${ENVIRONMENT}/prover-jobs-fri-datafix:${VERSION}"

LOCAL_PLATFORM="$(docker image inspect "$LOCAL_IMAGE" --format '{{.Os}}/{{.Architecture}}')"
if [ "$LOCAL_PLATFORM" != "$EXPECTED_PLATFORM" ]; then
    echo ">> Refusing to push $LOCAL_IMAGE: platform is $LOCAL_PLATFORM, expected $EXPECTED_PLATFORM." >&2
    echo ">> Rebuild with: PLATFORM=$EXPECTED_PLATFORM ./build.sh $VERSION" >&2
    exit 1
fi

docker tag "$LOCAL_IMAGE" "$REMOTE"

echo ">> About to push:"
echo "     local : $LOCAL_IMAGE"
echo "     remote: $REMOTE"
read -r -p ">> Type 'yes' to confirm push: " ack
if [ "$ack" != "yes" ]; then
    echo ">> Aborted; nothing pushed." >&2
    exit 1
fi

docker push "$REMOTE"
echo ">> pushed $REMOTE"
