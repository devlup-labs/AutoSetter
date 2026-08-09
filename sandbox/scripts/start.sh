#!/usr/bin/env bash
# ============================================================
# Anti-Gravity IDE — Start Script
# Builds image if needed, installs deps, and starts the server
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SERVER_DIR="$PROJECT_DIR/server"
IMAGE_NAME="mac-nsjail"

echo ""
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║       ⚡ ANTI-GRAVITY IDE — Start ⚡          ║"
echo "  ╚═══════════════════════════════════════════════╝"
echo ""

# Check Docker is running
if ! docker info >/dev/null 2>&1; then
  echo "❌ Docker is not running. Please start Docker Desktop and try again."
  exit 1
fi

# Build image if it doesn't exist
if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
  echo "📦 Image '$IMAGE_NAME' not found. Building..."
  bash "$SCRIPT_DIR/build.sh"
fi

# Install Node.js dependencies
echo "📚 Installing Node.js dependencies..."
cd "$SERVER_DIR"
npm install --silent
echo "✅ Dependencies installed."
echo ""

# Start the server
echo "🚀 Starting Anti-Gravity IDE server..."
echo ""
node src/server.js
