#!/bin/bash
# Build a pinned, self-contained OpenAI Codex CLI bundle for SWE-bench.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_DIR="${1:-${SCRIPT_DIR}/bundle}"
CODEX_VERSION="0.144.5"

case "$(uname -m)" in
    x86_64)
        TARGET="x86_64-unknown-linux-musl"
        PACKAGE_SHA256="23a7022a493c5404c50c62a4ad5655836adbee019d93c73114954d8daff20053"
        ;;
    aarch64|arm64)
        TARGET="aarch64-unknown-linux-musl"
        PACKAGE_SHA256="7703bbb6cbd4ba3df60c32d200bca2987691047353d3a6c825af2b8bc99f1808"
        ;;
    *)
        echo "ERROR: Unsupported architecture: $(uname -m)"
        exit 1
        ;;
esac

PACKAGE="codex-package-${TARGET}.tar.gz"
CACHE_DIR="${SWE_CODEX_CACHE_DIR:-${SCRIPT_DIR}/.cache}"
PACKAGE_PATH="${CACHE_DIR}/${CODEX_VERSION}/${PACKAGE}"
PACKAGE_URL="https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/${PACKAGE}"

echo "=== Building Codex agent bundle ==="
echo "Codex: ${CODEX_VERSION} (${TARGET})"
echo "Output: ${BUNDLE_DIR}"

mkdir -p "$(dirname "$PACKAGE_PATH")"
chmod 700 "$CACHE_DIR" "$(dirname "$PACKAGE_PATH")"

if [ ! -f "$PACKAGE_PATH" ] || ! echo "${PACKAGE_SHA256}  ${PACKAGE_PATH}" | sha256sum -c - >/dev/null 2>&1; then
    echo "Downloading official Codex release package..."
    DOWNLOAD_TMP=$(mktemp "${PACKAGE_PATH}.download.XXXXXX")
    if ! curl -fL --retry 3 --retry-delay 2 "$PACKAGE_URL" -o "$DOWNLOAD_TMP"; then
        rm -f "$DOWNLOAD_TMP"
        exit 1
    fi
    if ! echo "${PACKAGE_SHA256}  ${DOWNLOAD_TMP}" | sha256sum -c - >/dev/null; then
        echo "ERROR: Downloaded Codex package failed checksum verification." >&2
        rm -f "$DOWNLOAD_TMP"
        exit 1
    fi
    mv "$DOWNLOAD_TMP" "$PACKAGE_PATH"
fi

echo "${PACKAGE_SHA256}  ${PACKAGE_PATH}" | sha256sum -c -
mkdir -p "$BUNDLE_DIR"
find "$BUNDLE_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
tar -xzf "$PACKAGE_PATH" -C "$BUNDLE_DIR" --no-same-owner

cp "${SCRIPT_DIR}/config.toml" "${BUNDLE_DIR}/config.toml"
cp "${SCRIPT_DIR}/entrypoint.sh" "${BUNDLE_DIR}/entrypoint.sh"
chmod +x "${BUNDLE_DIR}/entrypoint.sh" "${BUNDLE_DIR}/bin/codex"

VERIFY_HOME="${CACHE_DIR}/verify-home"
rm -rf "$VERIFY_HOME"
mkdir -p "${VERIFY_HOME}/.codex"
CODEX_VERSION_OUTPUT=$(HOME="$VERIFY_HOME" CODEX_HOME="${VERIFY_HOME}/.codex" \
    "${BUNDLE_DIR}/bin/codex" --version 2>/dev/null)
[ "$CODEX_VERSION_OUTPUT" = "codex-cli ${CODEX_VERSION}" ] || {
    echo "ERROR: Unexpected Codex version: ${CODEX_VERSION_OUTPUT}" >&2
    exit 1
}
rm -rf "$VERIFY_HOME"
echo "Codex: ${CODEX_VERSION_OUTPUT}"
echo "Bundle: $(du -sh "$BUNDLE_DIR" | cut -f1)"
echo "=== Codex bundle build complete ==="
