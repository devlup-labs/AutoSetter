// ============================================================
// AutoSetter — Sandbox configuration
// Every containment and resource knob in one place.
// ============================================================

// Compiler settings. These must match autosetter/config.py (CPP_STANDARD,
// CPP_OPTIMIZATION): the two backends used to disagree (-O3/c++20 here,
// -O2/c++17 there), so code could build in local mode and fail over HTTP.
const CPP_STANDARD = process.env.CPP_STANDARD || "c++17";
const CPP_OPTIMIZATION = process.env.CPP_OPTIMIZATION || "-O2";

// ─── Container containment ──────────────────────────────────
//
// The pool used to start workers with --privileged, which grants all
// capabilities and host device access — effectively root on the host. Running
// model-written C++ in such a container undoes the point of having a sandbox.
//
// nsjail needs to create namespaces, which inside Docker needs either
// unprivileged user namespaces (best) or CAP_SYS_ADMIN plus an unconfined
// seccomp profile (what is used below). If nsjail refuses to start on your
// host, set SANDBOX_PRIVILEGED=1 to fall back to the old behaviour while you
// work out which capability is missing — and treat that as a temporary state,
// not a setting.
const PRIVILEGED = process.env.SANDBOX_PRIVILEGED === "1";

const CONTAINER_LIMITS = {
  // A runaway allocation should kill one container, not the host.
  memory: process.env.SANDBOX_MEMORY || "512m",
  // Without a swap limit equal to memory, Docker allows twice the RAM.
  memorySwap: process.env.SANDBOX_MEMORY_SWAP || "512m",
  cpus: process.env.SANDBOX_CPUS || "1",
  // The cheapest defence against a fork bomb there is.
  pidsLimit: process.env.SANDBOX_PIDS || "128",
  ramdiskSize: process.env.SANDBOX_RAMDISK || "50M",
};

// Submitted code has no reason to reach the network, and taking it away
// removes a whole category of abuse (exfiltration, using the judge as a proxy).
const NETWORK_MODE = process.env.SANDBOX_NETWORK || "none";

// ─── Per-process limits, applied by nsjail ──────────────────
//
// The time limit is per submission and passed in at call time. These are the
// ceilings that apply regardless of it.
const NSJAIL_LIMITS = {
  // Address space, MB. Bounds a single process; the container memory limit
  // bounds all of them together.
  addressSpaceMb: process.env.NSJAIL_AS_MB || "512",
  // Largest file the jailed process may write, MB.
  fileSizeMb: process.env.NSJAIL_FSIZE_MB || "64",
  // Open file descriptors.
  openFiles: process.env.NSJAIL_NOFILE || "64",
  // Processes/threads the jailed program may create.
  processes: process.env.NSJAIL_NPROC || "32",
};

// Compilation is not a safe operation on untrusted source: `#include` of an
// arbitrary path leaks its contents through error messages, and template
// recursion exhausts memory. It gets its own, more generous, limits.
const COMPILE_LIMITS = {
  timeoutSec: parseInt(process.env.SANDBOX_COMPILE_TIMEOUT || "30", 10),
  addressSpaceMb: process.env.NSJAIL_COMPILE_AS_MB || "2048",
  fileSizeMb: process.env.NSJAIL_COMPILE_FSIZE_MB || "128",
};

module.exports = {
  CPP_STANDARD,
  CPP_OPTIMIZATION,
  PRIVILEGED,
  CONTAINER_LIMITS,
  NETWORK_MODE,
  NSJAIL_LIMITS,
  COMPILE_LIMITS,
};
