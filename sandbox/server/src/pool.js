// ============================================================
// AutoSetter — Container Pool Manager
// Manages a pool of pre-warmed Docker containers with tmpfs
// ============================================================

const { execSync } = require("child_process");
const { PRIVILEGED, CONTAINER_LIMITS, NETWORK_MODE } = require("./config");

const DOCKER_IMAGE = process.env.SANDBOX_IMAGE || "autosetter-nsjail";
const CONTAINER_PREFIX = "autosetter-worker";

class ContainerPool {
  constructor(size = 3) {
    this.poolSize = size;
    this.idleQueue = []; // Container names ready to use
    this.busySet = new Set(); // Container names currently in use
    this.nextId = 1;
    this._shuttingDown = false;
  }

  /**
   * Initialize the pool by starting `poolSize` containers.
   * Each container gets the capabilities nsjail needs (not --privileged),
   * hard memory/CPU/PID ceilings, no network, and a tmpfs RAM disk.
   */
  async initPool() {
    console.log(`🚀 Initializing container pool (size: ${this.poolSize})...`);

    // Clean up any leftover containers from previous runs
    this._cleanupStaleContainers();

    const startPromises = [];
    for (let i = 0; i < this.poolSize; i++) {
      startPromises.push(this._startContainer());
    }

    await Promise.all(startPromises);
    console.log(
      `✅ Pool ready: ${this.idleQueue.length} workers standing by.\n`
    );
  }

  /**
   * Acquire an idle container from the pool.
   * Returns the container name, or waits if all are busy.
   */
  async acquire(timeoutMs = 10000) {
    const startTime = Date.now();

    while (this.idleQueue.length === 0) {
      if (Date.now() - startTime > timeoutMs) {
        throw new Error(
          "Pool exhausted: no idle containers available (timeout)"
        );
      }
      // Poll every 50ms
      await new Promise((resolve) => setTimeout(resolve, 50));
    }

    const containerName = this.idleQueue.shift();
    this.busySet.add(containerName);

    return containerName;
  }

  /**
   * Release a container back to the idle pool after cleaning its ramdisk.
   */
  async release(containerName) {
    try {
      // Wipe the ramdisk so it's clean for the next submission
      execSync(`docker exec ${containerName} sh -c 'rm -rf /ramdisk/*'`, {
        timeout: 5000,
      });
      this.busySet.delete(containerName);
      this.idleQueue.push(containerName);
    } catch (err) {
      console.error(
        `⚠️  Container ${containerName} failed during cleanup, replacing...`
      );
      this.busySet.delete(containerName);
      this._destroyContainer(containerName);

      if (!this._shuttingDown) {
        await this._startContainer();
      }
    }
  }

  /**
   * Get current pool status.
   */
  getStatus() {
    return {
      total: this.idleQueue.length + this.busySet.size,
      idle: this.idleQueue.length,
      busy: this.busySet.size,
      workers: {
        idle: [...this.idleQueue],
        busy: [...this.busySet],
      },
    };
  }

  /**
   * Scale the pool up or down.
   */
  async scale(newSize) {
    if (newSize < 1) throw new Error("Pool size must be at least 1");

    const currentTotal = this.idleQueue.length + this.busySet.size;

    if (newSize > currentTotal) {
      // Scale up
      const toAdd = newSize - currentTotal;
      console.log(`📈 Scaling up: adding ${toAdd} workers...`);
      const promises = [];
      for (let i = 0; i < toAdd; i++) {
        promises.push(this._startContainer());
      }
      await Promise.all(promises);
    } else if (newSize < currentTotal) {
      // Scale down — only remove idle containers
      const toRemove = Math.min(
        currentTotal - newSize,
        this.idleQueue.length
      );
      console.log(`📉 Scaling down: removing ${toRemove} idle workers...`);
      for (let i = 0; i < toRemove; i++) {
        const name = this.idleQueue.pop();
        if (name) this._destroyContainer(name);
      }
    }

    this.poolSize = newSize;
  }

  /**
   * Gracefully destroy all containers in the pool.
   */
  async destroyPool() {
    this._shuttingDown = true;
    console.log("\n🛑 Shutting down container pool...");

    const allContainers = [...this.idleQueue, ...this.busySet];
    for (const name of allContainers) {
      this._destroyContainer(name);
    }

    this.idleQueue = [];
    this.busySet.clear();
    console.log("✅ All workers stopped.");
  }

  // ─── Private Methods ─────────────────────────────────────

  async _startContainer() {
    const name = `${CONTAINER_PREFIX}-${this.nextId++}`;

    // --privileged gives a container every capability and the host's devices,
    // which is close to handing it root. nsjail only needs to create
    // namespaces: CAP_SYS_ADMIN plus an unconfined seccomp profile covers
    // that. The escape hatch exists because the exact requirement varies by
    // kernel — see config.js.
    const containment = PRIVILEGED
      ? "--privileged"
      : "--cap-add=SYS_ADMIN --security-opt seccomp=unconfined " +
        "--security-opt apparmor=unconfined";

    // Ceilings, so one bad submission cannot take the host with it. None of
    // these existed before: a fork bomb or a runaway allocation from generated
    // C++ hit the machine directly.
    const limits = [
      `--memory=${CONTAINER_LIMITS.memory}`,
      `--memory-swap=${CONTAINER_LIMITS.memorySwap}`,
      `--cpus=${CONTAINER_LIMITS.cpus}`,
      `--pids-limit=${CONTAINER_LIMITS.pidsLimit}`,
      `--network=${NETWORK_MODE}`,
    ].join(" ");

    try {
      execSync(
        `docker run -d ${containment} ${limits} ` +
          `--name ${name} ` +
          `--tmpfs /ramdisk:rw,exec,size=${CONTAINER_LIMITS.ramdiskSize} ` +
          `${DOCKER_IMAGE} sleep infinity`,
        { timeout: 30000 }
      );
      this.idleQueue.push(name);
      console.log(`   ✓ Started ${name}`);
    } catch (err) {
      console.error(`   ✗ Failed to start ${name}: ${err.message}`);
      if (!PRIVILEGED) {
        console.error(
          "     If nsjail cannot create namespaces on this host, set " +
            "SANDBOX_PRIVILEGED=1 to fall back — and treat that as temporary."
        );
      }
      throw err;
    }
  }

  _destroyContainer(name) {
    try {
      execSync(`docker rm -f ${name}`, { timeout: 10000, stdio: "pipe" });
      console.log(`   ✓ Stopped ${name}`);
    } catch {
      // Container may already be gone
    }
  }

  _cleanupStaleContainers() {
    try {
      const output = execSync(
        `docker ps -a --filter "name=${CONTAINER_PREFIX}-" --format "{{.Names}}"`,
        { timeout: 5000 }
      )
        .toString()
        .trim();

      if (output) {
        const staleNames = output.split("\n");
        console.log(
          `🧹 Cleaning ${staleNames.length} stale container(s)...`
        );
        for (const name of staleNames) {
          this._destroyContainer(name);
        }
      }
    } catch {
      // Ignore cleanup errors
    }
  }
}

module.exports = ContainerPool;
