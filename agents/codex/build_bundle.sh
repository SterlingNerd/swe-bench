#!/bin/bash
# Build a pinned, self-contained OpenAI Codex CLI bundle for SWE-bench.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_DIR="${1:-${SCRIPT_DIR}/bundle}"
CODEX_VERSION="0.144.3"

case "$(uname -m)" in
    x86_64)
        TARGET="x86_64-unknown-linux-musl"
        PACKAGE_SHA256="1c3c1f1f636da56a197ce0b5084d44b86f58f0fb32983278fa55c2544d221af4"
        ;;
    aarch64|arm64)
        TARGET="aarch64-unknown-linux-musl"
        PACKAGE_SHA256="d91c6354ec1efc125068056c02bc8cffbc0a53fd4ecfebc3e5f1771765fc7fa3"
        ;;
    *)
        echo "ERROR: Unsupported architecture: $(uname -m)"
        exit 1
        ;;
esac

PACKAGE="codex-package-${TARGET}.tar.gz"
PACKAGE_PATH="/tmp/${PACKAGE}"
PACKAGE_URL="https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/${PACKAGE}"

echo "=== Building Codex agent bundle ==="
echo "Codex: ${CODEX_VERSION} (${TARGET})"
echo "Output: ${BUNDLE_DIR}"

mkdir -p "$BUNDLE_DIR"
find "$BUNDLE_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +

if [ ! -f "$PACKAGE_PATH" ] || ! echo "${PACKAGE_SHA256}  ${PACKAGE_PATH}" | sha256sum -c - >/dev/null 2>&1; then
    echo "Downloading official Codex release package..."
    curl -fL --retry 3 --retry-delay 2 "$PACKAGE_URL" -o "$PACKAGE_PATH"
fi

echo "${PACKAGE_SHA256}  ${PACKAGE_PATH}" | sha256sum -c -
tar -xzf "$PACKAGE_PATH" -C "$BUNDLE_DIR" --no-same-owner

cp "${SCRIPT_DIR}/config.toml" "${BUNDLE_DIR}/config.toml"
cp "${SCRIPT_DIR}/entrypoint.sh" "${BUNDLE_DIR}/entrypoint.sh"
chmod +x "${BUNDLE_DIR}/entrypoint.sh" "${BUNDLE_DIR}/bin/codex"

VERIFY_HOME="/tmp/codex-bundle-check"
mkdir -p "${VERIFY_HOME}/.codex"
CODEX_VERSION_OUTPUT=$(HOME="$VERIFY_HOME" CODEX_HOME="${VERIFY_HOME}/.codex" \
    "${BUNDLE_DIR}/bin/codex" --version 2>/dev/null)
echo "Codex: ${CODEX_VERSION_OUTPUT}"
echo "Bundle: $(du -sh "$BUNDLE_DIR" | cut -f1)"
echo "=== Codex bundle build complete ==="
