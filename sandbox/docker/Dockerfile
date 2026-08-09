# ============================================================
# AutoSetter Sandbox — Worker Container Image
# Multi-stage build: compile NsJail, then create lean runtime
# ============================================================

# --- Stage 1: Build NsJail from source ---
# Using Ubuntu 24.04 for kernel 6.8 headers (needed for
# PR_SCHED_CORE_SCOPE_THREAD_GROUP used by latest nsjail)
FROM ubuntu:24.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    autoconf \
    bison \
    flex \
    gcc \
    g++ \
    git \
    libprotobuf-dev \
    libnl-route-3-dev \
    libtool \
    make \
    pkg-config \
    protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/google/nsjail.git /nsjail \
    && cd /nsjail \
    && make -j$(nproc) \
    && cp /nsjail/nsjail /usr/local/bin/nsjail

# --- Stage 2: Lean runtime image ---
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Install only what's needed at runtime:
#   - g++ for compiling user C++ code
#   - libprotobuf32 and libnl for nsjail runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    g++ \
    libprotobuf32t64 \
    libnl-route-3-200 \
    && rm -rf /var/lib/apt/lists/*

# Copy the compiled nsjail binary from builder
COPY --from=builder /usr/local/bin/nsjail /usr/local/bin/nsjail

# Create the ramdisk mount point
RUN mkdir -p /ramdisk

# Default: keep the container alive, waiting for work
CMD ["sleep", "infinity"]
