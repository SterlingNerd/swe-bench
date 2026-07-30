#!/bin/bash
# ==============================================================================
# Build smoke-test agent bundle
# ==============================================================================
set -euo pipefail

BUNDLE_DIR="${1:?Usage: build_bundle.sh <bundle_dir>}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$BUNDLE_DIR/bin"
cp "${SCRIPT_DIR}/bundle/bin/smoke-agent" "$BUNDLE_DIR/bin/"
chmod +x "$BUNDLE_DIR/bin/smoke-agent"

echo "Built smoke-test bundle at ${BUNDLE_DIR}"
