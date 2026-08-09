// ============================================================
// AutoSetter — Code Executor
// Handles the compile → run → capture pipeline via Docker exec
// ============================================================

const { spawn } = require("child_process");
const {
  CPP_STANDARD,
  CPP_OPTIMIZATION,
  NSJAIL_LIMITS,
  COMPILE_LIMITS,
} = require("./config");

/**
 * Execute C++ code inside a pre-warmed container.
 *
 * Pipeline:
 *   1. Pipe code to /ramdisk/main.cpp (in RAM)
 *   2. Compile with g++, itself under nsjail
 *   3. Run the binary via nsjail with time, memory and process limits
 *   4. Capture stdout, stderr, timing, exit code
 *
 * @param {string} containerName - Name of the acquired worker container
 * @param {string} code - C++ source code
 * @param {string} stdin - Input to feed to the program
 * @param {number} timeLimit - Max execution time in seconds
 * @returns {Object} Execution result with timing
 */
async function executeCode(containerName, code, stdin = "", timeLimit = 2) {
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

    // ── Step 3: Compile ─────────────────────────────────────
    const compileStart = Date.now();
    const compileResult = await compileInJail(containerName);
    result.compileTimeMs = Date.now() - compileStart;

    if (compileResult.timedOut) {
      result.status = "compile_error";
      result.stderr = `Compilation exceeded ${COMPILE_LIMITS.timeoutSec}s and was killed.`;
      result.totalTimeMs = Date.now() - totalStart;
      return result;
    }
    if (compileResult.exitCode !== 0) {
      result.status = "compile_error";
      result.stderr = compileResult.stderr || "Compilation failed.";
      result.totalTimeMs = Date.now() - totalStart;
      return result;
    }

    // ── Step 4: Execute via NsJail ──────────────────────────
    const execStart = Date.now();
    const execResult = await runInNsJail(containerName, stdin, timeLimit);
    result.executeTimeMs = Date.now() - execStart;

    result.stdout = execResult.stdout;
    result.stderr = execResult.stderr;
    result.exitCode = execResult.exitCode;

    // A run that was killed is not a run that succeeded. The old code did
    // `exitCode: code || 0`, and `code` is null whenever a process dies by
    // signal — so a killed program was reported as exit 0, i.e. success.
    if (execResult.timedOut || execResult.signal === "SIGKILL") {
      result.status = "timeout";
      result.stderr = `Time Limit Exceeded (${timeLimit}s)`;
      result.exitCode = -1;
    } else if (execResult.signal) {
      result.status = "runtime_error";
      result.stderr =
        execResult.stderr || `Killed by signal ${execResult.signal}`;
      result.exitCode = -1;
    } else if (execResult.exitCode !== 0) {
      result.status = "runtime_error";
    }
  } catch (err) {
    result.status = "runtime_error";
    result.stderr = err.message;
    result.exitCode = -1;
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
 * Run one command inside the container, resolving with how it ended.
 *
 * Resolves rather than rejects on a non-zero exit, because "the program
 * failed" is a result, not an error. Distinguishes death by signal from a
 * non-zero exit, which is what tells a timeout apart from a wrong answer.
 */
function execInContainer(containerName, shellCommand, timeoutMs) {
  return new Promise((resolve, reject) => {
    const proc = spawn(
      "docker",
      ["exec", "-i", containerName, "sh", "-c", shellCommand],
      { stdio: ["pipe", "pipe", "pipe"] }
    );

    let stdout = "";
    let stderr = "";
    let timedOut = false;

    const timer = setTimeout(() => {
      timedOut = true;
      proc.kill("SIGKILL");
    }, timeoutMs);

    proc.stdout.on("data", (d) => (stdout += d.toString()));
    proc.stderr.on("data", (d) => (stderr += d.toString()));

    proc.on("close", (code, signal) => {
      clearTimeout(timer);
      resolve({
        stdout,
        stderr,
        // `code` is null when the process died by signal; passing that through
        // as a number would erase the distinction.
        exitCode: code === null ? -1 : code,
        signal: signal || null,
        timedOut,
      });
    });

    proc.on("error", (err) => {
      clearTimeout(timer);
      reject(err);
    });

    proc.stdin.end();
  });
}

/**
 * Compile the submitted source, with the compiler itself jailed.
 *
 * Compiling untrusted C++ is not a safe operation: `#include "/etc/shadow"`
 * reports the file's contents in the error message, and a few lines of
 * template recursion will consume every byte the machine has. The old code ran
 * g++ outside nsjail with only a wall-clock timeout.
 */
function compileInJail(containerName) {
  const jail = [
    "nsjail",
    "-Mo",
    "--chroot /",
    "--user 99999",
    "--group 99999",
    `-t ${COMPILE_LIMITS.timeoutSec}`,
    `--rlimit_as ${COMPILE_LIMITS.addressSpaceMb}`,
    `--rlimit_fsize ${COMPILE_LIMITS.fileSizeMb}`,
    "--quiet",
    "--",
  ].join(" ");

  const compile =
    `/usr/bin/g++ ${CPP_OPTIMIZATION} -std=${CPP_STANDARD} ` +
    `/ramdisk/main.cpp -o /ramdisk/program`;

  return execInContainer(
    containerName,
    `${jail} ${compile}`,
    // Give the wall clock a little more room than nsjail's own limit, so the
    // jail is what stops a long compile and we get its message.
    (COMPILE_LIMITS.timeoutSec + 5) * 1000
  );
}

/**
 * Execute the compiled binary inside NsJail for isolation.
 *
 * NsJail enforces:
 *   - an unprivileged user (99999:99999)
 *   - a CPU time limit
 *   - address space, file size, descriptor and process ceilings
 *   - its own network namespace, so the program cannot reach the network
 *
 * That last one is a change: the previous command passed
 * --disable_clone_newnet, which kept the container's networking, meaning
 * submitted code could open outbound connections.
 */
async function runInNsJail(containerName, stdin, timeLimit) {
  const jail = [
    "nsjail",
    "-Mo",
    "--chroot /",
    "--user 99999",
    "--group 99999",
    `-t ${timeLimit}`,
    `--rlimit_as ${NSJAIL_LIMITS.addressSpaceMb}`,
    `--rlimit_fsize ${NSJAIL_LIMITS.fileSizeMb}`,
    `--rlimit_nofile ${NSJAIL_LIMITS.openFiles}`,
    `--rlimit_nproc ${NSJAIL_LIMITS.processes}`,
    "--quiet",
    "--",
    "/ramdisk/program",
  ].join(" ");

  const command = stdin ? `cat /ramdisk/input.txt | ${jail}` : jail;

  const outcome = await execInContainer(
    containerName,
    command,
    // nsjail's -t should fire first; this is the backstop for nsjail itself
    // hanging, and it is why timedOut has to be reported rather than swallowed.
    (timeLimit + 5) * 1000
  );

  return { ...outcome, stderr: stripJailLogs(outcome.stderr) };
}

/**
 * Remove nsjail's own log lines from stderr.
 *
 * Matches only its actual prefix format. The previous filter dropped every
 * line containing the word "nsjail" anywhere, which would eat a program's own
 * output.
 */
function stripJailLogs(stderr) {
  return stderr
    .split("\n")
    .filter((line) => !/^\[[IWDEF]\]\[.*?\]/.test(line))
    .join("\n")
    .trim();
}

module.exports = { executeCode };
