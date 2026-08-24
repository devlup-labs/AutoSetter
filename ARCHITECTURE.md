# AutoSetter Architecture & Design Guide

AutoSetter is an automated pipeline that ingests competitive programming problem statement images or PDFs and produces fully structured, verified, Codeforces/Polygon-compatible problem packages.

---

## 1. High-Level System Architecture

```text
┌───────────────────────────────┐
│     Statement Image / PDF     │
└──────────────┬────────────────┘
               │  (1) Intake & Vision Extraction
               ▼
┌───────────────────────────────┐
│         problem.json          │  Structured Problem Specification
└──────────────┬────────────────┘
               │  (2) Artifact Code Generation (Ollama)
               ▼
┌───────────────────────────────┐
│      Generated Artifacts      │  statement.md, solution.cpp,
└──────────────┬────────────────┘  validator.cpp, generator.cpp, checker.cpp
               │  (3) Sandboxed Validation Engine
               ▼
┌───────────────────────────────┐
│      Test & Probe Engine      │  Compile ➜ Sample Ground Truth ➜
└──────────────┬────────────────┘  Generate ➜ Validate ➜ Solve ➜ Probe
               │  (4) Release Packaging
               ▼
┌───────────────────────────────┐
│      Release Package /        │  problem.json, statement.md, solutions/,
│        manifest.json          │  files/, tests/ (001.in/ans), report
└───────────────────────────────┘
```

---

## 2. Core Package Structure (`autosetter/`)

The repository adopts responsibility-based modular organization:

| Module | Responsibility |
|---|---|
| [`config.py`](file:///Users/shreshthdhimole/AutoSetter/autosetter/config.py) | Central configuration, paths, defaults, and environment variables. |
| [`llm.py`](file:///Users/shreshthdhimole/AutoSetter/autosetter/llm.py) | Low-level Ollama client wrapper for multimodal vision and text inference. |
| [`vision.py`](file:///Users/shreshthdhimole/AutoSetter/autosetter/vision.py) | Raster image processing (PNG/JPG) and offline PDF rasterization via PyMuPDF. |
| [`prompts.py`](file:///Users/shreshthdhimole/AutoSetter/autosetter/prompts.py) | Template loader and safe `{JSON}` substitution without string format hazards. |
| [`extractor.py`](file:///Users/shreshthdhimole/AutoSetter/autosetter/extractor.py) | Extraction of `problem.json` via VLM, markdown code fence stripping, schema validation. |
| [`generator.py`](file:///Users/shreshthdhimole/AutoSetter/autosetter/generator.py) | Downstream generation orchestrator for statement markdown and testlib C++ code. |
| [`sandbox.py`](file:///Users/shreshthdhimole/AutoSetter/autosetter/sandbox.py) | C++ compilation and execution backends (host `g++` and Docker+NsJail HTTP). |
| [`pipeline.py`](file:///Users/shreshthdhimole/AutoSetter/autosetter/pipeline.py) | Test generation, validation, jury answer solving, attribution, and checker probing. |
| [`packager.py`](file:///Users/shreshthdhimole/AutoSetter/autosetter/packager.py) | Assembly of release bundle, test pairing, and manifest creation. |
| [`polygon.py`](file:///Users/shreshthdhimole/AutoSetter/autosetter/polygon.py) | Authenticated Codeforces Polygon API v2 client and package publisher. |
| [`cli.py`](file:///Users/shreshthdhimole/AutoSetter/autosetter/cli.py) | Unified command-line interface and end-to-end execution runner. |
| [`include/testlib.h`](file:///Users/shreshthdhimole/AutoSetter/autosetter/include/testlib.h) | Vendored canonical Mike Mirzayanov C++ `testlib.h` header. |
| [`prompts/`](file:///Users/shreshthdhimole/AutoSetter/autosetter/prompts) | Domain prompt templates (`json_extraction`, `statement`, `validator`, etc.). |

---

## 3. The Four Pipeline Stages

### Stage 1: Intake & Extraction (`autosetter.extractor`)
- Ingests image files (`.png`, `.jpg`, `.jpeg`) or multi-page `.pdf` documents.
- Normalizes all images to lossless RGB PNG byte-strings.
- Prompts a local Qwen Vision-Language Model (`qwen2.5vl:3b`) using [`autosetter/prompts/json_extraction.txt`](file:///Users/shreshthdhimole/AutoSetter/autosetter/prompts/json_extraction.txt).
- Strips any markdown fences (` ```json `) and extracts the root JSON object.
- Validates that all top-level keys (`title`, `story`, `input_format`, `output_format`, `constraints`, `samples`, `time_limit`, `memory_limit`, `notes`) and sample structures (`input`, `output`, `explanation`) are present.

### Stage 2: Code & Statement Generation (`autosetter.generator`)
- Serializes `problem.json` and renders five specialized prompt templates:
  1. `statement.txt` ➔ `generated/statement.md`
  2. `validator.txt` ➔ `generated/validator.cpp` (uses `testlib.h`)
  3. `generator.txt` ➔ `generated/generator.cpp` (uses `testlib.h`)
  4. `solution.txt` ➔ `generated/solution.cpp` (optimal C++17 solution)
  5. `checker.txt` ➔ `generated/checker.cpp` (uses `testlib.h`)
- Runs text inference against a local coding model (`qwen2.5-coder:7b`).

### Stage 3: Sandboxed Validation & Attribution (`autosetter.pipeline`)
To avoid model self-hallucination (where flawed code agrees with flawed tests), the pipeline executes three safeguards:

1. **Sample Verification (Ground Truth)**:
   The official samples from `problem.json` are evaluated against `validator.cpp`. A correct validator must accept all samples. If it fails, the validator (or extracted constraints) is flagged as faulty.
2. **Attribution of Failures**:
   If the validator accepts official samples but rejects generated tests, the pipeline diagnoses **the generator** as the faulty component.
3. **Checker Flaw Probing**:
   Running a checker on the reference solution only tests whether $x == x$. AutoSetter probes the checker with deliberately faulty outputs:
   - *Empty output*: Must be rejected.
   - *Truncated output*: Must be rejected.
   - *Perturbed values* (numeric output $+ 1$): Must be rejected.
   - *Trailing garbage*: Advisory check for stream termination.
   If a checker accepts these flawed inputs, it is marked untrusted.

### Stage 4: Release Packaging (`autosetter.packager`)
- Collects all valid components into `out/package/`.
- Verifies that only complete `.in` and `.ans` pairs are packaged.
- Emits `manifest.json` with `ready_for_release` boolean:
  ```json
  {
    "problem_title": "Two Sum",
    "ready_for_release": true,
    "packaged_tests": 10,
    "excluded_tests": [],
    "files": [...],
    "validation": {
      "total_tests": 10,
      "passed_tests": 10,
      "all_passed": true,
      "validator_trusted": true,
      "checker_trusted": true,
      "diagnosis": ""
    }
  }
  ```

---

## 4. Sandbox Security & Isolation

Untrusted C++ compilation and execution must be securely sandboxed:

```text
Host / AutoSetter
      │ (HTTP POST /api/execute)
      ▼
Docker Container (Ubuntu 24.04 runtime)
   ├── Memory limit (e.g. 512MB)
   ├── CPU limit (e.g. 1 CPU core)
   ├── PID limit (128 max processes)
   ├── Network disabled (network_mode: none)
   └── Tmpfs /ramdisk (RAM-only file storage)
         │
         ▼
   NsJail Isolation
      ├── Time limit (CPU / Wall clock)
      ├── Memory ceiling (RLIMIT_AS)
      ├── Output size ceiling (RLIMIT_FSIZE)
      ├── File descriptor limits (RLIMIT_NOFILE)
      ├── Subprocess limits (RLIMIT_NPROC)
      └── Chroot mount with unprivileged user (99999)
```

---

## 5. Polygon API Integration (`autosetter.polygon`)

AutoSetter provides direct synchronization with Codeforces Polygon using HMAC-SHA512 authenticated API requests:
- Time and memory limit configuration
- Validator, generator, and checker file upload
- `testlib.h` resource registration
- Multi-solution uploads with verdict tags (`PA`, `OK`, `WA`, `TL`)
- LaTeX/Markdown statement upload
- Automated testset registration
