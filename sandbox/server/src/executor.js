// ============================================================
// Anti-Gravity IDE — Code Executor
// Handles the compile → run → capture pipeline via Docker exec
// ============================================================

const { execSync, spawn } = require("child_process");

/**
 * Execute C++ code inside a pre-warmed container.
 *
 * Pipeline:
 *   1. Pipe code to /ramdisk/main.cpp (in RAM)
 *   2. Compile with g++ -O3 -std=c++20
 *   3. Run the binary via nsjail with time limits
 *   4. Capture stdout, stderr, timing, exit code
 *
 * @param {string} containerName - Name of the acquired worker container
 * @param {string} code - C++ source code
 * @param {string} stdin - Input to feed to the program
 * @param {number} timeLimit - Max execution time in seconds
 * @returns {Object} Execution result with timing
 */
async function executeCode(
  containerName,
  code,
  stdin = "",
  timeLimit = 2
) {
  const result = {
    status: "success",
    stdout: "",
    stderr: "",
    compileTimeMs: 0,
    executeTimeMs: 0,
    totalTimeMs: 0,
    exitCode: 0,
  };

  const totalStart = Date.now();

  try {
    // ── Step 1: Pipe code to RAM disk ────────────────────────
    await pipeToContainer(containerName, code, "/ramdisk/main.cpp");

    // ── Step 2: Pipe stdin if provided ───────────────────────
    if (stdin) {
      await pipeToContainer(containerName, stdin, "/ramdisk/input.txt");
    }

    // ── Step 3: Compile with -O3 optimizations ──────────────
    const compileStart = Date.now();
    try {
      execSync(
        `docker exec ${containerName} g++ -O3 -std=c++20 /ramdisk/main.cpp -o /ramdisk/program`,
        { timeout: 30000, stdio: ["pipe", "pipe", "pipe"] }
      );
    } catch (compileErr) {
      result.status = "compile_error";
      result.stderr = compileErr.stderr
        ? compileErr.stderr.toString().trim()
        : compileErr.message;
      result.compileTimeMs = Date.now() - compileStart;
      result.totalTimeMs = Date.now() - totalStart;
      return result;
    }
    result.compileTimeMs = Date.now() - compileStart;

    // ── Step 4: Execute via NsJail ──────────────────────────
    const execStart = Date.now();
    try {
      const execResult = await runInNsJail(
        containerName,
        stdin,
        timeLimit
      );
      result.stdout = execResult.stdout;
      result.stderr = execResult.stderr;
      result.exitCode = execResult.exitCode;

      if (execResult.exitCode !== 0) {
        result.status = "runtime_error";
      }
    } catch (execErr) {
      if (
        execErr.message.includes("timeout") ||
        execErr.message.includes("killed")
      ) {
        result.status = "timeout";
        result.stderr = `Time Limit Exceeded (${timeLimit}s)`;
      } else {
        result.status = "runtime_error";
        result.stderr = execErr.stderr || execErr.message;
      }
    }
    result.executeTimeMs = Date.now() - execStart;
  } catch (err) {
    result.status = "runtime_error";
    result.stderr = err.message;
  }

  result.totalTimeMs = Date.now() - totalStart;
  return result;
}

/**
 * Pipe content into a file inside the container via stdin.
 * This avoids any disk I/O on the host — everything stays in RAM.
 */
function pipeToContainer(containerName, content, destPath) {
  return new Promise((resolve, reject) => {
    const proc = spawn(
      "docker",
      ["exec", "-i", containerName, "sh", "-c", `cat > ${destPath}`],
      { stdio: ["pipe", "pipe", "pipe"] }
    );

    let stderr = "";
    proc.stderr.on("data", (d) => (stderr += d.toString()));

    proc.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`Pipe to ${destPath} failed: ${stderr}`));
    });

    proc.on("error", reject);

    proc.stdin.write(content);
    proc.stdin.end();
  });
}

/**
 * Execute the compiled binary inside NsJail for isolation.
 * NsJail enforces:
 *   - Unprivileged user (99999:99999)
 *   - CPU time limit
 *   - Chroot isolation
 */
function runInNsJail(containerName, stdin, timeLimit) {
  return new Promise((resolve, reject) => {
    const nsjailCmd = stdin
      ? `cat /ramdisk/input.txt | nsjail -Mo --chroot / --user 99999 --group 99999 -t ${timeLimit} --disable_clone_newnet -- /ramdisk/program`
      : `nsjail -Mo --chroot / --user 99999 --group 99999 -t ${timeLimit} --disable_clone_newnet -- /ramdisk/program`;

    const proc = spawn(
      "docker",
      ["exec", "-i", containerName, "sh", "-c", nsjailCmd],
      {
        stdio: ["pipe", "pipe", "pipe"],
        timeout: (timeLimit + 5) * 1000, // Docker-level safety timeout
      }
    );

    let stdout = "";
    let stderr = "";

    proc.stdout.on("data", (d) => (stdout += d.toString()));
    proc.stderr.on("data", (d) => (stderr += d.toString()));

    proc.on("close", (code) => {
      // NsJail outputs its own logs to stderr — filter them out
      const cleanStderr = stderr
        .split("\n")
        .filter(
          (line) =>
            !line.startsWith("[I]") &&
            !line.startsWith("[W]") &&
            !line.startsWith("[D]") &&
            !line.startsWith("[E]") &&
            !line.includes("nsjail")
        )
        .join("\n")
        .trim();

      resolve({
        stdout: stdout.trim(),
        stderr: cleanStderr,
        exitCode: code || 0,
      });
    });

    proc.on("error", (err) => {
      reject(err);
    });

    // Close stdin immediately if no input
    proc.stdin.end();
  });
}

module.exports = { executeCode };
