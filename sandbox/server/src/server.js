// ============================================================
// Anti-Gravity IDE — Express HTTP Server
// Exposes API for code execution and pool management
// ============================================================

const express = require("express");
const path = require("path");
const cors = require("cors");
const ContainerPool = require("./pool");
const { executeCode } = require("./executor");

const app = express();
const PORT = process.env.PORT || 3000;
const POOL_SIZE = parseInt(process.env.POOL_SIZE || "3", 10);

// Middleware
app.use(cors());
app.use(express.json({ limit: "1mb" }));
app.use(express.static(path.join(__dirname, "..", "public")));

// Pool instance
const pool = new ContainerPool(POOL_SIZE);

// ─── API Routes ─────────────────────────────────────────────

/**
 * POST /api/execute
 * Submit code for execution.
 * Body: { code: string, language: string, stdin?: string, timeLimit?: number }
 */
app.post("/api/execute", async (req, res) => {
  const { code, language = "cpp", stdin = "", timeLimit = 2 } = req.body;

  if (!code || !code.trim()) {
    return res.status(400).json({
      status: "error",
      stderr: "No code provided.",
    });
  }

  if (language !== "cpp") {
    return res.status(400).json({
      status: "error",
      stderr: `Language '${language}' is not yet supported. Only 'cpp' is available.`,
    });
  }

  let containerName;
  try {
    // Acquire a pre-warmed container
    containerName = await pool.acquire(15000);

    console.log(
      `⚡ Executing on ${containerName} (${code.length} bytes)...`
    );

    // Run the pipeline
    const result = await executeCode(containerName, code, stdin, timeLimit);

    const statusEmoji =
      result.status === "success"
        ? "✅"
        : result.status === "compile_error"
        ? "🔴"
        : result.status === "timeout"
        ? "⏱️"
        : "⚠️";

    console.log(
      `${statusEmoji} Done: compile=${result.compileTimeMs}ms, exec=${result.executeTimeMs}ms, total=${result.totalTimeMs}ms`
    );

    res.json(result);
  } catch (err) {
    console.error("❌ Execution failed:", err.message);
    res.status(500).json({
      status: "error",
      stderr: err.message,
      compileTimeMs: 0,
      executeTimeMs: 0,
      totalTimeMs: 0,
    });
  } finally {
    // Release container back to pool
    if (containerName) {
      pool.release(containerName).catch((err) => {
        console.error("⚠️  Release error:", err.message);
      });
    }
  }
});

/**
 * GET /api/pool/status
 * Returns pool health information.
 */
app.get("/api/pool/status", (req, res) => {
  res.json(pool.getStatus());
});

/**
 * POST /api/pool/scale
 * Resize the pool. Body: { size: number }
 */
app.post("/api/pool/scale", async (req, res) => {
  const { size } = req.body;

  if (!size || size < 1 || size > 20) {
    return res.status(400).json({
      error: "Size must be between 1 and 20.",
    });
  }

  try {
    await pool.scale(size);
    res.json(pool.getStatus());
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

/**
 * GET /api/health
 * Health check endpoint.
 */
app.get("/api/health", (req, res) => {
  const status = pool.getStatus();
  res.json({
    status: "ok",
    uptime: process.uptime(),
    pool: status,
  });
});

// ─── Server Lifecycle ───────────────────────────────────────

async function start() {
  console.log(`
  ╔═══════════════════════════════════════════════╗
  ║          ⚡ ANTI-GRAVITY IDE ⚡               ║
  ║     High-Performance Code Execution Sandbox   ║
  ╚═══════════════════════════════════════════════╝
  `);

  try {
    // Initialize the container pool
    await pool.initPool();

    // Start the HTTP server
    app.listen(PORT, () => {
      console.log(`🌐 Server running at http://localhost:${PORT}`);
      console.log(`📡 API endpoint: http://localhost:${PORT}/api/execute`);
      console.log(`📊 Pool status:  http://localhost:${PORT}/api/pool/status\n`);
    });
  } catch (err) {
    console.error("❌ Failed to start:", err.message);
    console.error(
      "\n💡 Make sure Docker is running and the 'mac-nsjail' image is built."
    );
    console.error("   Run: bash scripts/build.sh\n");
    process.exit(1);
  }
}

// Graceful shutdown
async function shutdown(signal) {
  console.log(`\n📴 Received ${signal}, shutting down gracefully...`);
  await pool.destroyPool();
  process.exit(0);
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));

// Start
start();
