#!/bin/bash
# ==============================================================================
# Build a self-contained agent bundle for the pi coding agent.
#
# This creates a relocatable bundle directory containing:
#   - Node.js binary (pinned version)
#   - pi CLI and all npm dependencies
#   - Config files (settings.json, models.json, auth.json)
#   - entrypoint.sh shim
#
# Usage: ./build_bundle.sh [output_dir]
#   output_dir defaults to agents/pi/bundle/
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_DIR="${1:-${SCRIPT_DIR}/bundle}"
NODE_VERSION="22.14.0"

echo "=== Building pi agent bundle ==="
echo "Output: ${BUNDLE_DIR}"

# Clean previous bundle
rm -rf "${BUNDLE_DIR:?/*}"/*

mkdir -p "${BUNDLE_DIR}"

# --- Detect architecture ---
ARCH=$(uname -m)
case "$ARCH" in
    x86_64)  NODE_ARCH="x64" ;;
    aarch64) NODE_ARCH="arm64" ;;
    armv7l)  NODE_ARCH="armv7l" ;;
    *)       echo "ERROR: Unsupported architecture: $ARCH"; exit 1 ;;
esac

echo "Architecture: ${ARCH} -> Node.js ${NODE_ARCH}"

# --- Download and extract Node.js binary ---
NODE_TARBALL="/tmp/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz"

if [ ! -f "${BUNDLE_DIR}/bin/node" ]; then
    echo "Downloading Node.js ${NODE_VERSION} for ${NODE_ARCH}..."
    curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz" \
        -o "${NODE_TARBALL}" 2>/dev/null || {
            echo "WARNING: Node.js download failed, checking cache..."
            if [ -f "$NODE_TARBALL" ]; then
                echo "Using cached tarball."
            else
                echo "ERROR: Cannot download Node.js. Please install node manually or check network."
                exit 1
            fi
        }
    # Extract with --strip-components=1 to put files directly in BUNDLE_DIR
    tar -xJf "${NODE_TARBALL}" -C "${BUNDLE_DIR}" --strip-components=1 2>/dev/null || \
        tar -xf "${NODE_TARBALL}" -C "${BUNDLE_DIR}" --strip-components=1 2>/dev/null || {
            # Try gzip fallback
            gunzip -c "${NODE_TARBALL}" | tar -xf - -C "${BUNDLE_DIR}" --strip-components=1
        }
    rm -f "${NODE_TARBALL}"
fi

export PATH="${BUNDLE_DIR}/bin:${PATH}"
echo "Node.js: $(node --version)"
echo "npm: $(npm --version)"

# --- Install pi CLI locally in bundle ---
echo "Installing pi coding agent..."
cd "${BUNDLE_DIR}"
npm install --ignore-scripts "@earendil-works/pi-coding-agent@latest" 2>&1 | tail -3

# Create a wrapper script for pi that uses the bundled node
# Note: node_modules is at BUNDLE_DIR level, not bin/ level
cat > "${BUNDLE_DIR}/bin/pi" <<WRAPPER
#!/bin/bash
SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")/../" && pwd)"
exec "\${SCRIPT_DIR}/bin/node" "\${SCRIPT_DIR}/node_modules/@earendil-works/pi-coding-agent/dist/cli.js" "\$@"
WRAPPER
chmod +x "${BUNDLE_DIR}/bin/pi"

# --- Copy config files ---
echo "Copying config files..."
PI_SRC="${SCRIPT_DIR}/.pi"
AGENT_DIR="${BUNDLE_DIR}/.pi"
mkdir -p "${AGENT_DIR}"

if [ -f "${PI_SRC}/settings.json" ]; then
    cp "${PI_SRC}/settings.json" "${AGENT_DIR}/settings.json"
fi
if [ -f "${PI_SRC}/models.json" ]; then
    cp "${PI_SRC}/models.json" "${AGENT_DIR}/models.json"
fi
if [ -f "${PI_SRC}/auth.json" ]; then
    cp "${PI_SRC}/auth.json" "${AGENT_DIR}/auth.json"
fi

# Copy npm packages (loop-police, etc.)
if [ -d "${PI_SRC}/npm" ]; then
    cp -r "${PI_SRC}/npm/"* "${AGENT_DIR}/npm/" 2>/dev/null || true
fi

# --- Copy entrypoint.sh ---
echo "Copying entrypoint..."
cp "${SCRIPT_DIR}/entrypoint.sh" "${BUNDLE_DIR}/entrypoint.sh"
chmod +x "${BUNDLE_DIR}/entrypoint.sh"

# --- Verify bundle ---
echo ""
echo "=== Bundle contents ==="
du -sh "${BUNDLE_DIR}" 2>/dev/null || true
echo ""
echo "Node.js: $(ls ${BUNDLE_DIR}/bin/node 2>/dev/null && echo 'OK' || echo 'MISSING')"
echo "pi CLI:  $(ls ${BUNDLE_DIR}/bin/pi 2>/dev/null && echo 'OK' || echo 'MISSING')"
echo "settings: $(ls ${AGENT_DIR}/settings.json 2>/dev/null && echo 'OK' || echo 'MISSING')"
echo "models:   $(ls ${AGENT_DIR}/models.json 2>/dev/null && echo 'OK' || echo 'MISSING')"
echo "auth:     $(ls ${AGENT_DIR}/auth.json 2>/dev/null && echo 'OK' || echo 'MISSING')"
echo ""
echo "=== Bundle build complete ==="
