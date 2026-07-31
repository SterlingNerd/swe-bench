#!/usr/bin/env bash

# Exit immediately if any command fails
set -e

# Your local registry domain (Port 443 is assumed by default)
REGISTRY="docker-registry.sterling.digital"

# Check if a wildcard argument was provided
if [ -z "$1" ]; then
    echo "Error: Please provide an image wildcard pattern."
    echo "Usage: $0 'pattern*'"
    exit 1
fi

PATTERN="$1"

echo "Searching for local images matching pattern: '${PATTERN}'..."

# Extract matching local images (Format: REPOSITORY:TAG)
mapfile -t MATCHES < <(docker image ls --format "{{.Repository}}:{{.Tag}}" | grep -E "${PATTERN}" | grep -v "<none>")

if [ ${#MATCHES[@]} -eq 0 ]; then
    echo "No matching images found for pattern '${PATTERN}'."
    exit 0
fi

echo "Found ${#MATCHES[@]} matching image(s). Isolation push starting..."
echo "------------------------------------------------------------------"

for IMAGE in "${MATCHES[@]}"; do
    # Skip images that are already prefixed with our target registry
    if [[ "$IMAGE" == "$REGISTRY"* ]]; then
        echo "--> Skipping '${IMAGE}' (already prefixed with registry path)."
        continue
    fi

    TARGET_IMAGE="${REGISTRY}/${IMAGE}"

    echo "Processing: ${IMAGE}"
    echo "  -> Isolating native architecture and pushing directly..."

    # Use buildx to strip multi-architecture structures
    echo -e "FROM ${IMAGE}" | docker buildx build \
      --platform linux/amd64 \
      --tag "${TARGET_IMAGE}" \
      --push -

    echo "  ✓ Done with ${IMAGE}"
    echo "------------------------------------------------------------------"
done

echo "All matching images processed successfully!"

