#!/bin/bash
# Build script for Music Helper Swift daemon
# Usage: ./build.sh [--release|--debug]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BUILD_TYPE="${1:---release}"

echo "🔨 Building Music Helper ($BUILD_TYPE)..."

if [ "$BUILD_TYPE" == "--debug" ]; then
    swift build
    BINARY_PATH=".build/debug/music-helper"
else
    swift build -c release
    BINARY_PATH=".build/release/music-helper"
fi

if [ -f "$BINARY_PATH" ]; then
    echo "✅ Build successful: $BINARY_PATH"
    echo "📦 Binary size: $(du -h "$BINARY_PATH" | cut -f1)"

    # Make it executable
    chmod +x "$BINARY_PATH"

    # Show version info
    echo ""
    echo "📋 Swift version:"
    swift --version
else
    echo "❌ Build failed: binary not found at $BINARY_PATH"
    exit 1
fi
