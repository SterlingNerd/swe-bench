#!/bin/bash
set -e

BASE_IMAGE="swe-pi-base"
PI_IMAGE="swe-pi-sandbox"
CONTAINER="pi_swe_evaluator"

# Build base image (SWE-bench + workspace)
if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
    echo "Building base image '$BASE_IMAGE'..."
    docker build -f ../containers/Dockerfile.base -t "$BASE_IMAGE" ..
else
    echo "Base image '$BASE_IMAGE' already exists."
fi

# Build Pi image (on top of base)
if ! docker image inspect "$PI_IMAGE" >/dev/null 2>&1; then
    echo "Building Pi image '$PI_IMAGE'..."
    docker build -f ../containers/Dockerfile.pi -t "$PI_IMAGE" ..
else
    echo "Pi image '$PI_IMAGE' already exists."
fi

# Remove existing container if present
if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
    echo "Removing existing container '$CONTAINER'..."
    docker rm -f "$CONTAINER" >/dev/null
fi

# Run the container — locked down:
#   - Default bridge network (outbound internet only)
#   - host.docker.internal → host's IP (llama.cpp at :11434)
#   - Read-only root filesystem (tmpfs for /tmp and workspace)
#   - Drop all capabilities, add back only what's needed
#   - Memory limit (8GB), no new privileges
echo "Starting container '$CONTAINER'..."
docker run -it \
    --name "$CONTAINER" \
    --memory 8g \
    --memory-swap 8g \
    --pids-limit 500 \
    --tmpfs /tmp:rw,noexec,nosuid,size=2g \
    --tmpfs /home/agent/workspace:rw,noexec,nosuid,size=4g \
    --cap-drop ALL \
    --cap-add NET_RAW \
    --security-opt no-new-privileges:true \
    --add-host host.docker.internal:172.17.0.1 \
    --volume /var/run/docker.sock:/var/run/docker.sock \
    "$PI_IMAGE"
