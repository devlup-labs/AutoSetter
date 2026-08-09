#!/usr/bin/env bash
# ============================================================
# Anti-Gravity IDE — Build Script
# Builds the mac-nsjail Docker image with NsJail + g++
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DOCKER_DIR="$PROJECT_DIR/docker"
IMAGE_NAME="mac-nsjail"

echo ""
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║       ⚡ ANTI-GRAVITY IDE — Build ⚡          ║"
echo "  ╚═══════════════════════════════════════════════╝"
echo ""

# Check Docker is running
if ! docker info >/dev/null 2>&1; then
  echo "❌ Docker is not running. Please start Docker Desktop and try again."
  exit 1
fi

echo "🐳 Docker is running."
echo "📦 Building image '$IMAGE_NAME' from $DOCKER_DIR/Dockerfile..."
echo ""

# Build the image (native ARM64 on Apple Silicon)
docker build \
  -t "$IMAGE_NAME" \
  -f "$DOCKER_DIR/Dockerfile" \
  "$DOCKER_DIR"

echo ""
echo "✅ Image '$IMAGE_NAME' built successfully!"
echo ""
echo "   Image size: $(docker image inspect "$IMAGE_NAME" --format='{{.Size}}' | awk '{printf "%.0f MB\n", $1/1024/1024}')"
echo ""
echo "   Next: Run 'bash scripts/start.sh' to launch the server."
echo ""
