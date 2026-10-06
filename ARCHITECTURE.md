# AutoSetter Architecture & Design Guide

AutoSetter takes a competitive programming statement (image or PDF) and produces a verified Polygon package. It skips problems that already exist in its vector database and pushes new packages to Codeforces Polygon.

---

## 1. Deployment topology

```text
 Setter's machine                                         GPU server
┌──────────────────────────────────────────┐            ┌───────────────────────────┐
│ autosetter CLI                           │  SSH       │ Ollama  (localhost:11434) │
│   ├─ Ollama client ── localhost:11434 ───┼─ tunnel ──►│   ├─ qwen3-vl:32b         │
│   │                  (ssh -N -L ...)     │  (port 22) │   └─ Qwen3-Coder-Next     │
│   ├─ embedder: bge-small-en-v1.5         │            └───────────────────────────┘
│   ├─ g++ sandbox / Docker+NsJail pool    │
│   └─ Polygon client ─────────────────────┼── HTTPS ──► polygon.codeforces.com/api
│                                          │
│ Qdrant (localhost:6333 or QDRANT_URL)  ◄─┼── vector database of existing problems
└──────────────────────────────────────────┘
```

- **Models.** Vision `qwen3-vl:32b` and text/code `Qwen3-Coder-Next:latest` are served by Ollama on a GPU server. Ollama's port is not exposed. `autosetter.remote.SSHTunnel` runs `ssh -N -L <local>:localhost:<remote> <host>` for the length of a run, and the Ollama client talks to `http://localhost:<local>`. The host can be `user@host` or a `~/.ssh/config` alias, so keys, ports and jump hosts stay in normal SSH configuration. If the local port is already listening, the existing tunnel or local Ollama is reused. Without `--ssh`, `--host` / `OLLAMA_HOST` points at any reachable Ollama.
- **Embeddings** (`BAAI/bge-small-en-v1.5`) run locally in the CLI process. This must be the same model that built the Qdrant collection.
- **Untrusted code** (generated C++) runs in the local `g++` sandbox, or in the Docker + NsJail pool (§6).
- **Polygon** is called over HTTPS with HMAC-SHA512-signed requests, using credentials entered when the run starts.

---

## 2. End-to-end workflow

```text
autosetter statement.png --ssh <host>
   │
   ├─ (0) CLI setup ─────────────────────────────── autosetter.cli
   │     ├─ prompt for Polygon API key + secret (env values offered as defaults)
   │     ├─ verify them with Polygon (problems.list), up to 3 attempts
   │     └─ open the SSH tunnel to Ollama
   │
   ├─ (1) Intake & vision extraction ────────────── autosetter.extractor  [qwen3-vl:32b]
   │     image/PDF ─► problem.json (validated schema)
   │
   ├─ (2) Existing-problem check ─────────────────── autosetter.similarity  [Qdrant]
   │     embed problem.json ─► KNN search, k=1 ─► cosine similarity s
   │     s > 0.80 ─► print link, exit 3 (no generation, no upload)   [--force overrides]
   │     database unavailable ─► warn and continue
   │
   ├─ (3) Artifact generation ────────────────────── autosetter.generator  [Qwen3-Coder-Next]
   │     statement.md, solution.cpp (+ greedy/brute/heavy), validator.cpp,
   │     checker.cpp, test_spec.json (checked against the official samples)
   │
   ├─ (4) Sandboxed validation + self-healing ────── autosetter.pipeline
   │     compile ─► samples vs validator ─► Z3 tests ─► validate ─► solve ─► probe checker
   │     failures ─► regenerate only the files at fault, with feedback (≤ 3 iterations)
   │
   ├─ (5) Polygon packaging ──────────────────────── autosetter.packager
   │     out/package/: problem.json, statement, solutions/, files/, samples/, tests/,
   │     script, validation_report.json, manifest.json (ready_for_release)
   │
   └─ (6) Polygon publish ────────────────────────── autosetter.polygon
         only if ready_for_release (or --push-unverified):
         problem.create ─► limits ─► checker/validator/testlib ─► generator ─► solutions
         ─► statement ─► samples + tests ─► script ─► tags ─► commitChanges ─► buildPackage
```

Credentials are collected in step 0, not after packaging. This way a long unattended run does not stop at the end to wait for input, and bad credentials fail in seconds instead of after the pipeline.

`autosetter.runner.generate_from_image` runs steps 1–5 and does not depend on the command line. Other front ends (scripts, a web UI) can call it and then `autosetter.polygon.publish_package`.

---

## 3. Package structure (`autosetter/`)

| Module | Responsibility |
|---|---|
| [cli.py](autosetter/cli.py) | Argument parsing, Polygon credential prompt and verification, SSH tunnel lifetime, upload decision, exit codes. |
| [runner.py](autosetter/runner.py) | Pipeline driver (steps 1–5) returning `PipelineResult` (report, similar problems, `existing_problem`). |
| [config.py](autosetter/config.py) | Defaults and environment overrides: models, Ollama host, SSH, similarity (k, threshold), Qdrant, Polygon. |
| [remote.py](autosetter/remote.py) | `SSHTunnel`: local port forward to Ollama on the GPU server; reuses an open port. |
| [llm.py](autosetter/llm.py) | Ollama client for multimodal vision and text inference. |
| [vision.py](autosetter/vision.py) | PNG/JPG normalization and PDF rasterization (PyMuPDF). |
| [prompts.py](autosetter/prompts.py) | Template loader with safe `{JSON}` substitution. |
| [extractor.py](autosetter/extractor.py) | VLM extraction of `problem.json`, fence stripping, schema validation. |
| [similarity.py](autosetter/similarity.py) | `ProblemDatabase` (shared Qdrant connection + embedder), k-NN search, `find_existing_problem` (score > threshold). |
| [generator.py](autosetter/generator.py) | Artifact generation and targeted regeneration with feedback. |
| `testgen/` | Z3 test generation from `test_spec.json`. |
| [sandbox.py](autosetter/sandbox.py) | C++ compilation and execution (host `g++`, Docker+NsJail HTTP). |
| [pipeline.py](autosetter/pipeline.py) | Validation engine: samples, tests, jury answers, blame, checker probes. |
| [packager.py](autosetter/packager.py) | Polygon package assembly (incl. official samples) and manifest. |
| [polygon.py](autosetter/polygon.py) | Polygon API client, problem creation, full upload, commit and build; standalone `python -m autosetter.polygon`. |
| [prompts/](autosetter/prompts) | Prompt templates. |
| [include/testlib.h](autosetter/include/testlib.h) | Vendored testlib. |

Outside the package:

| Path | Responsibility |
|---|---|
| [vector_database/](vector_database/) | Offline pipeline that collects Codeforces/LeetCode problems, normalizes and deduplicates them, embeds them, and uploads them to Qdrant (cosine distance). [search.py](vector_database/search.py) holds `ProblemSearcher`, which `autosetter.similarity` uses at run time. |
| [sandbox/](sandbox/) | Docker + NsJail execution pool. |

---

## 4. Pipeline stages in detail

### Stage 1: Intake & extraction (`autosetter.extractor`)
- Accepts `.png`, `.jpg`, `.jpeg` or multi-page `.pdf`, normalized to RGB PNG.
- Prompts the vision model (`qwen3-vl:32b`) with [json_extraction.txt](autosetter/prompts/json_extraction.txt).
- Requires `title`, `story`, `input_format`, `output_format`, `constraints`, `samples` (`input`, `output`, `explanation`), `time_limit`, `memory_limit` and `notes`.

### Stage 2: Existing-problem check (`autosetter.similarity`)
- `problem.json` becomes a `vector_database` `Problem` and is embedded with the same text layout as the stored problems (`build_embedding_text`).
- `ProblemDatabase.connect()` opens Qdrant and checks the collection **before** loading the embedding model, so an unreachable database fails fast. The connection is cached per (URL, collection, model).
- **KNN with k=1**: only the single nearest stored problem is retrieved (`AUTOSETTER_SIMILARITY_K`, default 1).
- **Threshold**: the collection uses cosine distance over L2-normalized vectors, so Qdrant's score is cosine similarity. A score **strictly greater than 0.80** (`AUTOSETTER_SIMILARITY_THRESHOLD`) marks the problem as existing. The runner then returns early with `existing_problem` set (link from the stored `url`, or built from the Codeforces ID), and the CLI prints the link and exits with code 3.
- The matches are written to `out/similar_problems.json`. Missing dependencies, a down Qdrant or a missing collection raise `SimilaritySearchError`, which only skips the check.

### Stage 3: Artifact generation (`autosetter.generator`)
Text/code model `Qwen3-Coder-Next:latest`:

| Prompt | Output |
|---|---|
| `statement.txt` | `statement.md` |
| `validator.txt` | `validator.cpp` (testlib) |
| `test_spec.txt` | `test_spec.json` (Z3 input spec; retried with errors if it rejects the samples) |
| `solution.txt` | `solution.cpp` (main solution) |
| `solution_greedy.txt` / `solution_brute.txt` / `solution_heavy.txt` | wrong / slow solutions |
| `checker.txt` | `checker.cpp` (testlib) |

### Stage 4: Sandboxed validation & attribution (`autosetter.pipeline`)
1. **Sample ground truth.** The validator must accept every official sample, or the validator/constraints are flagged.
2. **Blame.** If the validator accepts the samples but rejects a generated test, the generator is at fault.
3. **Checker probing.** Empty, truncated and perturbed outputs must be rejected; trailing garbage is advisory.

The runner turns failures into targeted regeneration requests with the error text as feedback, for up to 3 iterations.

### Stage 5: Polygon packaging (`autosetter.packager`)
- Copies `problem.json`, the statement, all solutions, validator/checker/generator or spec, and `testlib.h`.
- Writes the **official samples** to `samples/NN.in` / `NN.ans`. Polygon uploads them as the first tests and shows them in the statement.
- Ships only complete `.in`/`.ans` test pairs. A Polygon `script` is emitted only for a testlib `generator.cpp`; Z3 tests ship as files.
- `manifest.json` records files, `packaged_tests`, `packaged_samples`, excluded tests, the validation summary and `ready_for_release`.

### Stage 6: Polygon publish (`autosetter.polygon`)
| Step | API method | Source |
|---|---|---|
| Create problem (unless `--polygon-problem-id`) | `problem.create` | title → `lowercase-dashed` name; timestamp suffix if the name is taken |
| Limits | `problem.updateInfo` | `time_limit` → ms (0.25–15 s), `memory_limit` → MB (4–1024) |
| Checker / validator | `problem.saveFile` + `setChecker` / `setValidator` | `files/` |
| testlib | `problem.saveFile` (resource) | `files/testlib.h` |
| Generator | `problem.saveFile` | `files/generator.cpp` / `.py` |
| Solutions | `problem.saveSolution` | `solution.cpp`=MA, `greedy`=WA, `brute`/`heavy`=TL |
| Statement | `problem.saveStatement` | legend/input/output/notes from `problem.json` (fallback: `statement.md`) |
| Tests | `problem.saveTest` | `samples/` (in statements) then `tests/` |
| Script, tags | `problem.saveScript`, `problem.saveTags` | `script`, `tags.txt` |
| Commit, build | `problem.commitChanges`, `problem.buildPackage` (verify) | |

Every request is signed: `apiSig = rand6 + sha512(rand6/method?sorted_params#secret)`.

**Credential handling.** The CLI prompts with `input()` for the key and `getpass()` for the secret, offering `POLYGON_API_KEY` / `POLYGON_SECRET` as defaults. It verifies them with a `problems.list` call and keeps them only in memory. Non-interactive runs use the environment only. Unverified packages are not uploaded unless `--push-unverified` is passed.

---

## 5. CLI contract

| Exit | Meaning |
|---|---|
| 0 | Verified package (uploaded if credentials were given) |
| 1 | Fatal error (input, models, SSH, rejected credentials) |
| 2 | Package built but not fit for release (not uploaded by default) |
| 3 | Problem already exists (link printed) |
| 4 | Verified package, Polygon upload failed |

---

## 6. Sandbox security & isolation

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

## 7. Configuration reference

| Variable | Default | Used by |
|---|---|---|
| `AUTOSETTER_VISION_MODEL` | `qwen3-vl:32b` | extraction |
| `AUTOSETTER_TEXT_MODEL` | `Qwen3-Coder-Next:latest` | generation |
| `OLLAMA_HOST` / `AUTOSETTER_OLLAMA_HOST` | `http://localhost:11434` | Ollama client (without SSH) |
| `AUTOSETTER_SSH_HOST` | *(unset: no tunnel)* | `remote.SSHTunnel` |
| `AUTOSETTER_SSH_PORT` / `_KEY` / `_LOCAL_PORT` / `_REMOTE_PORT` / `_TIMEOUT` | `22` / ssh default / `11434` / `11434` / `60` | `remote.SSHTunnel` |
| `AUTOSETTER_SIMILARITY` | `1` | enable existing-problem check |
| `AUTOSETTER_SIMILARITY_K` | `1` | KNN k |
| `AUTOSETTER_SIMILARITY_THRESHOLD` | `0.80` | cosine threshold |
| `QDRANT_URL` / `QDRANT_COLLECTION` / `QDRANT_API_KEY` | `http://localhost:6333` / `competitive_programming_problems` / — | database connection |
| `EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` | embeddings |
| `POLYGON_API_KEY` / `POLYGON_SECRET` | — | Polygon (defaults offered at the prompt) |
| `POLYGON_API_URL` | `https://polygon.codeforces.com/api/` | Polygon |
| `AUTOSETTER_NUM_TESTS`, `AUTOSETTER_TIMEOUT`, `AUTOSETTER_Z3_*` | see [config.py](autosetter/config.py) | pipeline |
