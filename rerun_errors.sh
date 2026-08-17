#!/bin/bash
# Re-run errored and container errored instances for PI agent

set -euo pipefail

AGENT="pi"
TIMEOUT=3600

# Errored instances (3)
ERRORED=(
    "astropy__astropy-8707"
    "astropy__astropy-8872"
    "django__django-11066"
)

# Container error instances (7)
CONTAINER_ERRORED=(
    "django__django-12406"
    "django__django-13449"
    "django__django-14351"
    "django__django-15022"
    "django__django-15128"
    "django__django-15280"
    "django__django-16263"
)

ALL_TO_RERUN=("${ERRORED[@]}" "${CONTAINER_ERRORED[@]}")

echo "=== Re-running ${#ALL_TO_RERUN[@]} instances for agent '$AGENT' ==="
echo "Errored: ${ERRORED[*]}"
echo "Container errors: ${CONTAINER_ERRORED[*]}"
echo ""

# Check if run.sh exists
if [[ ! -f "./run.sh" ]]; then
    echo "Error: run.sh not found in current directory"
    exit 1
fi

# Run each instance
for iid in "${ALL_TO_RERUN[@]}"; do
    echo "=========================================="
    echo "Running $iid (${#ALL_TO_RERUN[@]} total)..."
    echo "=========================================="
    
    # Remove old result files to force re-run
    rm -rf "workspace/outputs/$AGENT/$iid"
    
    # Run the instance
    ./run.sh run "$AGENT" "$iid" --timeout "$TIMEOUT"
    
    echo "Completed $iid"
    echo ""
done

echo "=== All re-runs complete ==="
echo "Run evaluation with: ./run.sh eval $AGENT"
