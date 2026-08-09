#!/usr/bin/env bash
# ============================================================
# AutoSetter Sandbox — Stop Script
# Gracefully stops the server and removes all worker containers
# ============================================================
set -euo pipefail

CONTAINER_PREFIX="autosetter-worker"

echo ""
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║       ⚡ AUTOSETTER SANDBOX — Stop ⚡           ║"
echo "  ╚═══════════════════════════════════════════════╝"
echo ""

# Kill any running Node.js server
if pgrep -f "node src/server.js" >/dev/null 2>&1; then
  echo "🛑 Stopping Node.js server..."
  pkill -f "node src/server.js" || true
  echo "   ✓ Server stopped."
else
  echo "ℹ️  No running server found."
fi

# Remove all worker containers
echo "🧹 Cleaning up worker containers..."
CONTAINERS=$(docker ps -a --filter "name=${CONTAINER_PREFIX}-" --format "{{.Names}}" 2>/dev/null || true)

if [ -n "$CONTAINERS" ]; then
  echo "$CONTAINERS" | while read -r name; do
    docker rm -f "$name" >/dev/null 2>&1 || true
    echo "   ✓ Removed $name"
  done
else
  echo "   No worker containers found."
fi

echo ""
echo "✅ AutoSetter Sandbox stopped."
echo ""
